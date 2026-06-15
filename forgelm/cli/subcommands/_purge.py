"""``forgelm purge`` subcommand (Phase 21 — GDPR Article 17 erasure).

Implements the operator-facing surface specified in
``docs/design/gdpr_erasure.md``:

- ``forgelm purge --row-id <id> --corpus <path>`` — atomic JSONL row
  erasure with hashed audit event (Article 17 right-to-erasure for
  training corpus rows).
- ``forgelm purge --run-id <id> --kind {staging,artefacts}`` —
  run-scoped artefact erasure (staging directory or compliance bundle).
- ``forgelm purge --check-policy [--config <path>]`` — read-only
  retention-policy violation report against the loaded config's
  ``retention:`` block.

Audit-event vocabulary (six new events per design §5.1):

- ``data.erasure_requested`` — emitted FIRST, before any deletion;
  carries the hashed ``target_id`` so a forensic reviewer sees the
  intent even if the deletion itself fails.
- ``data.erasure_completed`` — emitted LAST after the disk operation
  succeeded; carries ``bytes_freed`` + ``files_modified`` + (corpus
  mode) ``pre_erasure_line_number``.
- ``data.erasure_failed`` — emitted when the disk operation raised;
  the chain shows ``request → fail`` instead of ``request → complete``,
  so an operator scanning the log can spot the unfinished erasure.
- ``data.erasure_warning_memorisation`` — corpus row erasure ran
  while a ``final_model/`` exists for any run that consumed this
  corpus.  The row is gone from disk but may still be memorised in
  the trained weights.
- ``data.erasure_warning_synthetic_data_present`` — corpus row erasure
  ran with synthetic-data snapshots present; the row may have produced
  derivative snippets the row→snippet mapping no longer connects.
- ``data.erasure_warning_external_copies`` — the loaded config has a
  non-empty ``webhook`` block; downstream consumers may have received
  notices that referenced the now-erased data.

Exit-code contract (per ``docs/standards/error-handling.md``):

- 0 — success or ``--check-policy`` report (gate-not-report semantic
  per design §10 Q5).
- 1 — config error (unknown ``row-id``, missing corpus, mutually
  exclusive flag combination, conflicting retention values).
- 2 — runtime error (I/O, permission denied, atomic rename failure).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Tuple

from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_SUCCESS, EXIT_TRAINING_ERROR
from .._logging import logger

# Audit event vocabulary (design §5.1).  Centralised so a future rename
# cannot drift across the dispatcher and the test fixtures.
_EVT_ERASURE_REQUESTED = "data.erasure_requested"
_EVT_ERASURE_COMPLETED = "data.erasure_completed"
_EVT_ERASURE_FAILED = "data.erasure_failed"
_EVT_WARN_MEMORISATION = "data.erasure_warning_memorisation"
_EVT_WARN_SYNTHETIC_DATA = "data.erasure_warning_synthetic_data_present"
_EVT_WARN_EXTERNAL_COPIES = "data.erasure_warning_external_copies"

# Persistent per-output-dir salt file.  Mode 0600.  Design §5.4
# F-R5-05: persistent regardless of FORGELM_AUDIT_SECRET presence so
# subsequent invocations without the env var still hash deterministically.
_SALT_FILENAME = ".forgelm_audit_salt"
_SALT_BYTES = 16
_SALT_FILE_MODE = 0o600

# Corpus row id keys we accept on erasure.  Operators with id-less corpora
# must pre-populate ids via ``forgelm audit --add-row-ids`` (Phase 28
# follow-up; line-number fallback is rejected per design §4.2 to defend
# against silent wrong-row deletion on a re-ordered file).
_ROW_ID_KEYS: Tuple[str, ...] = ("id", "row_id")

# Allowed --kind values per design §4.1.  ``logs`` is intentionally
# absent (audit logs are append-only Article 17(3)(b) and never deleted
# automatically — the trainer + the tool record the request, the
# operator handles the residual disposal manually).
_VALID_RUN_KINDS: Tuple[str, ...] = ("staging", "artefacts")

# Cap for the ``error_message`` field persisted into the append-only audit
# chain.  Mirrors ``reverse_pii._AUDIT_ERROR_MESSAGE_MAX`` so the two GDPR
# subcommands bound the field identically (F-P5-OPUS-07).
_AUDIT_ERROR_MESSAGE_MAX = 200


def _sanitise_audit_error_message(message: str) -> str:
    """Mask + redact + bound ``error_message`` before it enters the chain.

    ``data.erasure_failed`` carries ``error_message=str(exc)`` for an
    ``OSError`` raised mid-erasure.  A Python exception string can embed
    secret-bearing header values or, worse, corpus row content (emails /
    phone numbers / names) when a downstream library quotes the offending
    line — which would land that PII in the append-only audit log
    permanently (CLAUDE.md principle 5).  ``docs/design/gdpr_erasure.md``
    §6 mandates this field be routed through
    :func:`forgelm._http._mask_secrets_in_text` and then the
    :mod:`forgelm.data_audit._pii_regex` mask helpers; reverse-pii already
    length-bounds its sibling field.  This helper applies all three passes
    (secret mask → PII regex mask → length bound) so the load-bearing
    privacy mandate and the length-bound precedent are met together
    (F-P5-OPUS-07).
    """
    from forgelm._http import _mask_secrets_in_text
    from forgelm.data_audit._pii_regex import mask_pii

    # ``_mask_secrets_in_text`` needs a header map to know which values to
    # strip; the purge path has no outbound headers, so pass ``None`` (it
    # short-circuits to a no-op) per design §6's explicit note.
    masked = _mask_secrets_in_text(message, None)
    masked = mask_pii(masked)
    if len(masked) <= _AUDIT_ERROR_MESSAGE_MAX:
        return masked
    return masked[:_AUDIT_ERROR_MESSAGE_MAX] + "…[truncated]"


def _output_error_and_exit(output_format: str, msg: str, exit_code: int) -> NoReturn:
    """Emit *msg* as a structured JSON error or a log record, then exit.

    Mirrors the helpers in :mod:`._approve` and :mod:`._approvals` so
    the JSON envelope contract is identical across the compliance
    family of subcommands.  ``-> NoReturn`` so the type checker knows
    control never returns.
    """
    if output_format == "json":
        print(json.dumps({"success": False, "error": msg}))
    else:
        logger.error(msg)
    sys.exit(exit_code)


# ---------------------------------------------------------------------------
# Salt resolution + target_id hashing (design §5.4)
# ---------------------------------------------------------------------------


def _read_persistent_salt(output_dir: str) -> bytes:
    """Read (or create) the per-output-dir persistent salt — no env XOR.

    Companion to :func:`_resolve_salt`; this helper exposes JUST the
    on-disk persistent half of the salt resolution so callers that
    explicitly want per-dir-only semantics (e.g. ``forgelm reverse-pii
    --salt-source per_dir``) can bypass the env-var XOR step that
    ``_resolve_salt`` performs unconditionally.

    Raises :class:`OSError` when the salt file cannot be created or
    read; the caller surfaces the I/O error as ``EXIT_TRAINING_ERROR``.
    """
    salt_path = os.path.join(output_dir, _SALT_FILENAME)
    if not os.path.isfile(salt_path):
        os.makedirs(output_dir, exist_ok=True)
        # Atomic create-only write so two concurrent purges cannot both
        # generate a different salt and race each other.  ``O_EXCL``
        # guarantees the open fails if the file appeared since the
        # ``isfile`` check.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(salt_path, flags, _SALT_FILE_MODE)
        except FileExistsError:
            # Another process created it between the check and our
            # open — fall through to read.
            pass
        else:
            try:
                os.write(fd, secrets.token_bytes(_SALT_BYTES))
            finally:
                os.close(fd)
    with open(salt_path, "rb") as fh:
        persistent = fh.read(_SALT_BYTES)
    if len(persistent) < _SALT_BYTES:
        # Truncated / corrupted salt file — refuse rather than silently
        # produce a weak hash.
        raise OSError(
            f"Salt file {salt_path!r} is shorter than {_SALT_BYTES} bytes "
            "(corrupted or truncated).  Delete the file to regenerate."
        )
    return persistent


def _resolve_salt(output_dir: str) -> Tuple[bytes, str]:
    """Return ``(salt_bytes, salt_source)`` for ``target_id`` hashing.

    Salt resolution per design §5.4:

    - The persistent per-output-dir salt at
      ``<output_dir>/.forgelm_audit_salt`` is **always** consulted
      (created on first call with mode 0600).
    - When ``FORGELM_AUDIT_SECRET`` is set, its first 16 bytes are
      XOR'd with the persistent salt to derive the actual hashing
      salt; ``salt_source`` is recorded as ``"env_var"`` so a salt-
      source toggle between invocations is detectable in the chain
      via the ``salt_source`` audit-event field.
    - When the env var is absent, the persistent salt is used
      verbatim; ``salt_source = "per_dir"``.

    Raises :class:`OSError` when the salt file cannot be created or
    read; the caller surfaces the I/O error as ``EXIT_TRAINING_ERROR``.
    """
    persistent = _read_persistent_salt(output_dir)
    env_secret = os.environ.get("FORGELM_AUDIT_SECRET", "").encode("utf-8")
    if env_secret:
        env_prefix = env_secret[:_SALT_BYTES].ljust(_SALT_BYTES, b"\x00")
        # XOR the env-var prefix with the persistent salt.  Either
        # source alone is insufficient; both together produce the
        # actual hashing salt.
        salt = bytes(a ^ b for a, b in zip(persistent, env_prefix))
        return salt, "env_var"
    return persistent, "per_dir"


def _hash_target_id(raw_value: str, salt: bytes) -> str:
    """Hex SHA-256 of ``salt + raw_value``.  Stable, side-effect free."""
    return hashlib.sha256(salt + raw_value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Corpus row erasure (--row-id / --corpus)
# ---------------------------------------------------------------------------


def _validate_row_id_args(args, output_format: str) -> None:
    """Refuse --row-id invocations that would silently delete the wrong row.

    Design §4.2 rejects three patterns at the CLI:

    - line-number values (``--row-id 42`` where 42 looks numeric and
      no JSONL ``id`` field exists is the legacy line-number form;
      we refuse to disambiguate);
    - directory-mode corpus paths (multi-file purges loop in operator
      script, not in this tool);
    - bare invocation without ``--corpus``.
    """
    if not args.corpus:
        _output_error_and_exit(
            output_format,
            "`--row-id` requires `--corpus <path>`. Bare row-id invocations are "
            "rejected per Article 17 (per-row decision + per-row audit event).",
            EXIT_CONFIG_ERROR,
        )
    # ``--corpus`` is trusted operator self-service input (named directly on the
    # CLI; design §4.5 scopes purge as a single-node operator tool), so it is NOT
    # boundary-checked against output_dir — unlike the run-scoped staging path,
    # which the design explicitly promises to constrain (F-P5-OPUS-16). Note that
    # ``os.path.isfile`` follows symlinks, so a ``--corpus`` symlink would have
    # its TARGET rewritten on match; that is operator-chosen input here.
    if not os.path.isfile(args.corpus):
        _output_error_and_exit(
            output_format,
            f"Corpus file not found: {args.corpus!r}.  Multi-file purges are an "
            "operator script (loop over files), not a `forgelm purge` mode — see "
            "design §4.1 rationale.",
            EXIT_CONFIG_ERROR,
        )


def _find_matching_rows(corpus_path: str, row_id: str) -> List[Tuple[int, Dict[str, Any]]]:
    """Locate rows whose ``id`` (or ``row_id``) field equals ``row_id``.

    Returns ``[(line_number, row_dict)]`` in append order.  Skips
    malformed (non-JSON, non-dict-root) lines silently — they are
    auditor's problem, not ours, and they cannot match by definition.
    """
    matches: List[Tuple[int, Dict[str, Any]]] = []
    with open(corpus_path, "r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            for key in _ROW_ID_KEYS:
                if str(row.get(key, "")) == row_id:
                    matches.append((line_no, row))
                    break
    return matches


def _atomic_rewrite_dropping_lines(corpus_path: str, line_numbers_to_drop: List[int]) -> int:
    """Rewrite ``corpus_path`` excluding the given 1-based line numbers.

    Uses a temp file in the same directory + ``os.replace`` for atomic
    swap — operators get either the full pre-erasure file or the full
    post-erasure file, never a partial state.  Returns the byte count
    freed.

    Raises :class:`OSError` on any I/O failure; the caller is responsible
    for emitting ``data.erasure_failed`` and surfacing
    ``EXIT_TRAINING_ERROR``.
    """
    drop_set = set(line_numbers_to_drop)
    pre_size = os.path.getsize(corpus_path)
    parent = os.path.dirname(os.path.abspath(corpus_path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".forgelm_purge_", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            with open(corpus_path, "r", encoding="utf-8") as src:
                for line_no, line in enumerate(src, start=1):
                    if line_no in drop_set:
                        continue
                    out.write(line)
            # Wave 2b Round-4 review F-W2B-03: flush + fsync the temp
            # file's data blocks to disk BEFORE the namespace swap.
            # ``os.replace`` is rename-atomic on POSIX, but without an
            # explicit fsync the temp file's *contents* may still be
            # buffered in the page cache.  A power loss between the
            # swap and the cache flush would leave the corpus inode
            # pointing at the new file with its data blocks unwritten
            # — i.e. an empty corpus.  Mirrors the discipline in
            # `forgelm/data_audit/_orchestrator.py::_atomic_write_json`.
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp_path, corpus_path)
        # Wave 2b final-followup F-21-04: also fsync the parent
        # directory so the rename's *directory entry* is on disk.
        # On non-journaled FS (FAT, certain tmpfs configurations),
        # a power loss between the rename and the directory metadata
        # flush can leave the directory entry unrecoverable; on
        # journaled FS (ext4, xfs, apfs, ntfs) the journal handles
        # this — but the cost is one open/fsync/close, so prefer
        # belt-and-suspenders.  ``O_DIRECTORY`` not supported on
        # Windows, where directory fds are also a no-op; trap and
        # continue.
        try:
            dir_fd = os.open(parent, os.O_DIRECTORY)
        except (AttributeError, OSError):  # pragma: no cover — Windows / unusual FS
            pass
        else:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except OSError:
        # Best-effort cleanup of the temp file if the swap failed.
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:  # pragma: no cover — defensive
                logger.debug("Could not clean up purge temp file %s", tmp_path)
        raise
    post_size = os.path.getsize(corpus_path)
    return pre_size - post_size


def _detect_warning_conditions(
    output_dir: str,
    config_loaded: Optional[Any],
) -> Tuple[List[str], Dict[str, Any]]:
    """Return ``(warning_event_names, extra_fields_per_event)`` for the
    warning events that should fire alongside ``data.erasure_completed``.

    Per design §5.1:

    - ``data.erasure_warning_memorisation`` when any ``final_model/``
      exists in the operator's output dir tree (a trained model may
      have memorised the row).
    - ``data.erasure_warning_synthetic_data_present`` when any
      ``synthetic_data*.jsonl`` exists (the row may have produced
      derivative snippets).
    - ``data.erasure_warning_external_copies`` when the loaded config
      has a non-empty ``webhook`` block (downstream consumers may
      have received notices).
    """
    warnings: List[str] = []
    extras: Dict[str, Any] = {}

    final_model_dir = os.path.join(output_dir, "final_model")
    if os.path.isdir(final_model_dir):
        warnings.append(_EVT_WARN_MEMORISATION)
        extras["affected_run_ids"] = _scan_run_ids_with_final_model(output_dir)

    synthetic_files = sorted(str(p) for p in Path(output_dir).glob("synthetic_data*.jsonl") if p.is_file())
    if synthetic_files:
        warnings.append(_EVT_WARN_SYNTHETIC_DATA)
        extras["synthetic_files"] = synthetic_files

    webhook_targets = _extract_webhook_targets(config_loaded)
    if webhook_targets:
        warnings.append(_EVT_WARN_EXTERNAL_COPIES)
        extras["webhook_targets"] = webhook_targets

    return warnings, extras


def _scan_run_ids_with_final_model(output_dir: str) -> List[str]:
    """Best-effort: enumerate run ids whose ``final_model/`` is present.

    Looks for ``final_model.staging.<run_id>/`` directories left as
    forensic artefacts after promotion.

    Wave 2b Round-4 review F-W2B-06 fix: once an operator runs
    ``forgelm approve <run_id>``, the staging directory is renamed to
    ``final_model/`` — leaving no staging-suffix directory to match.
    Falls back to the audit log's ``human_approval.granted`` events so
    the warning event still names a concrete run id.  Empty list with
    a sentinel placeholder when nothing matches and the audit log is
    silent — the warning still fires (the ``final_model/`` is present,
    that's what triggered it), but the operator gets a clear hint.
    """
    pattern = re.compile(r"final_model\.staging\.([A-Za-z0-9._-]+)$")
    run_ids: List[str] = []
    try:
        for entry in os.listdir(output_dir):
            match = pattern.match(entry)
            if match:
                run_ids.append(match.group(1))
    except OSError:
        return []
    if run_ids:
        return sorted(set(run_ids))
    # Staging dir is gone (run was promoted).  Walk the audit log for
    # granted approvals; their run_id is the one that consumed this
    # corpus.
    audit_run_ids = _scan_run_ids_from_granted_events(os.path.join(output_dir, "audit_log.jsonl"))
    if audit_run_ids:
        return audit_run_ids
    # Nothing on disk, nothing in audit chain — surface a clear hint
    # so the operator knows where to look next.
    return ["unknown — no matching staging directory or human_approval.granted event"]


def _scan_run_ids_from_granted_events(audit_log_path: str) -> List[str]:
    """Best-effort: scan ``audit_log.jsonl`` for ``human_approval.granted``
    events; return their ``run_id`` values, deduplicated + sorted.
    """
    if not os.path.isfile(audit_log_path):
        return []
    run_ids: List[str] = []
    try:
        with open(audit_log_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("event") != "human_approval.granted":
                    continue
                run_id = event.get("run_id")
                if isinstance(run_id, str) and run_id:
                    run_ids.append(run_id)
    except OSError:
        return []
    return sorted(set(run_ids))


def _extract_webhook_targets(config_loaded: Optional[Any]) -> List[str]:
    """Return redacted webhook URLs from the loaded config, or ``[]``.

    Reads ``config.webhook.url`` (literal value) and resolves
    ``config.webhook.url_env`` to the env var's value when set; the
    matching ``WebhookConfig`` schema (``forgelm/config.py``) only
    exposes those two URL-bearing fields.  Path / query (which may
    carry credentials) is redacted via :func:`forgelm._http._mask_netloc`
    so a `data.erasure_warning_external_copies` event does not leak the
    full webhook URL into the audit chain.
    """
    if config_loaded is None or getattr(config_loaded, "webhook", None) is None:
        return []
    webhook = config_loaded.webhook
    raw_targets: List[str] = []

    literal_url = getattr(webhook, "url", None)
    if literal_url:
        raw_targets.append(literal_url)

    env_var_name = getattr(webhook, "url_env", None)
    if env_var_name:
        env_value = os.environ.get(env_var_name)
        if env_value:
            raw_targets.append(env_value)

    targets: List[str] = []
    for url in raw_targets:
        try:
            from forgelm._http import _mask_netloc

            targets.append(_mask_netloc(url))
        except (ImportError, AttributeError, ValueError):
            # `_mask_netloc` is the documented redactor; if it cannot
            # parse / import, drop a generic placeholder so the chain
            # never carries the raw URL.  Caught exceptions narrowed
            # from `Exception` per Round-3 review to surface unrelated
            # bugs.
            targets.append("<webhook-url-redacted>")
    return sorted(set(targets))


def _validate_match_count_or_fail(
    matches: List[Tuple[int, Dict[str, Any]]],
    *,
    request_fields: Dict[str, Any],
    target_id_hash: str,
    audit: Any,
    args: Any,
    output_format: str,
) -> None:
    """Refuse no-match / multi-match-without-opt-in.  Emits
    ``data.erasure_failed`` to the chain BEFORE exiting so a forensic
    reviewer sees the refusal.

    The error messages echo a short prefix of ``target_id_hash``
    instead of the raw ``args.row_id`` so neither the audit-log
    ``error_message`` field nor the operator-facing stdout/JSON
    payload leak a potentially-PII identifier.

    The 12-char hex prefix (48 bits = ~2.8 × 10¹⁴ collision space) is
    enough headroom for any plausible single-operator-day failed-purge
    volume; the full 64-char hash is still recorded as the audit
    event's ``target_id`` field (built into ``request_fields`` upstream)
    for cross-tool correlation with ``forgelm reverse-pii``.  Do not
    crop below 12 (collision risk) or grow above 16 (maintain symmetry
    with the dry-run preview's 16-char rendering at ``_purge.py``'s
    success-summary site; the two consumers correlate on the shared
    prefix).
    """
    redacted = f"<id_hash:{target_id_hash[:12]}…>"
    if not matches:
        audit.log_event(
            _EVT_ERASURE_FAILED,
            **request_fields,
            error_class="NoMatchingRow",
            error_message=f"No row with id matching {redacted} found in corpus.",
        )
        _output_error_and_exit(
            output_format,
            f"No row with id={redacted} in {args.corpus!r}.  Refusing to delete.",
            EXIT_CONFIG_ERROR,
        )
    if len(matches) > 1 and getattr(args, "row_matches", "one") == "one":
        audit.log_event(
            _EVT_ERASURE_FAILED,
            **request_fields,
            error_class="MultiMatchRefused",
            error_message=f"{len(matches)} rows matched id={redacted}; --row-matches=one refuses ambiguity.",
            match_count=len(matches),
        )
        _output_error_and_exit(
            output_format,
            f"{len(matches)} rows matched id={redacted} in {args.corpus!r}; "
            "--row-matches defaults to 'one' (refuse on ambiguity).  Pass --row-matches=all to "
            "delete every match (operator confirms intent), or supply a unique id.",
            EXIT_CONFIG_ERROR,
        )


def _emit_row_dry_run(
    *,
    audit: Any,
    request_fields: Dict[str, Any],
    matches: List[Tuple[int, Dict[str, Any]]],
    pre_first_line: int,
    target_id_hash: str,
    salt_source: str,
    corpus_path: str,
    output_format: str,
) -> None:
    """Dry-run shortcut: do not touch the corpus; emit a completed
    event with ``dry_run=True`` so the chain reflects the intent."""
    audit.log_event(
        _EVT_ERASURE_COMPLETED,
        **request_fields,
        bytes_freed=0,
        files_modified=[],
        pre_erasure_line_number=pre_first_line,
        match_count=len(matches),
    )
    _emit_purge_success(
        output_format,
        {
            "mode": "row",
            "dry_run": True,
            "row_id_hash": target_id_hash,
            "salt_source": salt_source,
            "corpus_path": corpus_path,
            "matches": len(matches),
            "first_line": pre_first_line,
            "warnings": [],
        },
    )


def _perform_row_erasure_and_audit(
    *,
    audit: Any,
    request_fields: Dict[str, Any],
    args: Any,
    output_dir: str,
    line_numbers: List[int],
    pre_first_line: int,
    matches: List[Tuple[int, Dict[str, Any]]],
    target_id_hash: str,
    salt_source: str,
    config_loaded: Any,
    output_format: str,
) -> None:
    """Atomic rewrite + completion + warning audit emission.

    Centralises the post-validation half of :func:`_run_purge_row_id`
    so the dispatcher stays under SonarCloud S3776 cognitive
    complexity ceiling.
    """
    try:
        bytes_freed = _atomic_rewrite_dropping_lines(args.corpus, line_numbers)
    except OSError as exc:
        audit.log_event(
            _EVT_ERASURE_FAILED,
            **request_fields,
            error_class=exc.__class__.__name__,
            error_message=_sanitise_audit_error_message(str(exc)),
        )
        _output_error_and_exit(
            output_format,
            f"Atomic rewrite of {args.corpus!r} failed: {exc}.  Corpus left unchanged.",
            EXIT_TRAINING_ERROR,
        )

    warning_events, warning_extras = _detect_warning_conditions(output_dir, config_loaded)
    audit.log_event(
        _EVT_ERASURE_COMPLETED,
        **request_fields,
        bytes_freed=bytes_freed,
        files_modified=[os.path.abspath(args.corpus)],
        pre_erasure_line_number=pre_first_line,
        match_count=len(matches),
    )
    for event_name in warning_events:
        audit.log_event(event_name, **request_fields, **warning_extras)

    _emit_purge_success(
        output_format,
        {
            "mode": "row",
            "dry_run": False,
            "row_id_hash": target_id_hash,
            "salt_source": salt_source,
            "corpus_path": os.path.abspath(args.corpus),
            "matches": len(matches),
            "first_line": pre_first_line,
            "bytes_freed": bytes_freed,
            "warnings": warning_events,
        },
    )


def _run_purge_row_id(args, output_format: str) -> None:
    """Handle ``forgelm purge --row-id <id> --corpus <path>``."""
    _validate_row_id_args(args, output_format)
    output_dir = args.output_dir or os.path.dirname(os.path.abspath(args.corpus)) or "."
    os.makedirs(output_dir, exist_ok=True)

    try:
        salt, salt_source = _resolve_salt(output_dir)
    except OSError as exc:
        _output_error_and_exit(
            output_format,
            f"Could not resolve audit salt for {output_dir!r}: {exc}",
            EXIT_TRAINING_ERROR,
        )

    target_id_hash = _hash_target_id(args.row_id, salt)
    justification = args.justification or "(operator did not supply --justification)"

    # Late import so a fresh checkout without `forgelm[ingestion]` etc.
    # can still run `forgelm doctor` without hitting AuditLogger's
    # operator-identity validation.
    from forgelm.compliance import AuditLogger
    from forgelm.config import ConfigError

    try:
        audit = AuditLogger(output_dir)
    except ConfigError as exc:
        _output_error_and_exit(output_format, str(exc), EXIT_CONFIG_ERROR)

    config_loaded = _maybe_load_config(getattr(args, "config", None))

    request_fields: Dict[str, Any] = {
        "target_kind": "row",
        "target_id": target_id_hash,
        "salt_source": salt_source,
        "corpus_path": os.path.abspath(args.corpus),
        "justification": justification,
        "dry_run": bool(args.dry_run),
    }
    audit.log_event(_EVT_ERASURE_REQUESTED, **request_fields)

    matches = _find_matching_rows(args.corpus, args.row_id)
    _validate_match_count_or_fail(
        matches,
        request_fields=request_fields,
        target_id_hash=target_id_hash,
        audit=audit,
        args=args,
        output_format=output_format,
    )

    line_numbers = [ln for ln, _row in matches]
    pre_first_line = line_numbers[0]

    if args.dry_run:
        _emit_row_dry_run(
            audit=audit,
            request_fields=request_fields,
            matches=matches,
            pre_first_line=pre_first_line,
            target_id_hash=target_id_hash,
            salt_source=salt_source,
            corpus_path=os.path.abspath(args.corpus),
            output_format=output_format,
        )
        return

    _perform_row_erasure_and_audit(
        audit=audit,
        request_fields=request_fields,
        args=args,
        output_dir=output_dir,
        line_numbers=line_numbers,
        pre_first_line=pre_first_line,
        matches=matches,
        target_id_hash=target_id_hash,
        salt_source=salt_source,
        config_loaded=config_loaded,
        output_format=output_format,
    )


# ---------------------------------------------------------------------------
# Run-scoped erasure (--run-id / --kind)
# ---------------------------------------------------------------------------


def _run_purge_run_id(args, output_format: str) -> None:
    """Handle ``forgelm purge --run-id <id> --kind {staging,artefacts}``."""
    if args.kind not in _VALID_RUN_KINDS:
        _output_error_and_exit(
            output_format,
            f"--kind must be one of {sorted(_VALID_RUN_KINDS)!r}; got {args.kind!r}.",
            EXIT_CONFIG_ERROR,
        )
    output_dir = args.output_dir or "."
    if not os.path.isdir(output_dir):
        _output_error_and_exit(
            output_format,
            f"--output-dir not found: {output_dir!r}.",
            EXIT_CONFIG_ERROR,
        )

    from forgelm.compliance import AuditLogger
    from forgelm.config import ConfigError

    try:
        audit = AuditLogger(output_dir, run_id=args.run_id)
    except ConfigError as exc:
        _output_error_and_exit(output_format, str(exc), EXIT_CONFIG_ERROR)

    request_fields: Dict[str, Any] = {
        "target_kind": args.kind,
        "target_id": args.run_id,
        "output_dir": os.path.abspath(output_dir),
        "justification": args.justification or "(operator did not supply --justification)",
        "dry_run": bool(args.dry_run),
    }
    audit.log_event(_EVT_ERASURE_REQUESTED, **request_fields)

    target_paths = _resolve_run_kind_targets(output_dir, args.run_id, args.kind)
    if not target_paths:
        audit.log_event(
            _EVT_ERASURE_FAILED,
            **request_fields,
            error_class="NoMatchingArtefacts",
            error_message=f"No {args.kind!r} artefacts found for run_id={args.run_id!r}.",
        )
        _output_error_and_exit(
            output_format,
            f"No {args.kind!r} artefacts found for run_id={args.run_id!r} under {output_dir!r}.",
            EXIT_CONFIG_ERROR,
        )

    # Boundary check (F-P5-OPUS-02): refuse any resolved target that escapes
    # ``output_dir``.  ``--run-id`` is free-form, so a ``..``-bearing value
    # could point the deletion at an arbitrary sibling directory; reject the
    # whole batch (fail-closed — never a partial delete) before either the
    # dry-run report or the real deletion runs.
    escaping = [p for p in target_paths if not _path_inside_output_dir(p, output_dir)]
    if escaping:
        audit.log_event(
            _EVT_ERASURE_FAILED,
            **request_fields,
            error_class="PathTraversalRefused",
            error_message=_sanitise_audit_error_message(
                f"Refusing to erase target(s) outside output_dir: {[str(p) for p in escaping]!r}."
            ),
        )
        _output_error_and_exit(
            output_format,
            (
                f"Refusing run-scoped erasure: run_id={args.run_id!r} resolves to "
                f"{len(escaping)} target(s) outside output_dir {output_dir!r}. "
                "Path traversal is not permitted."
            ),
            EXIT_CONFIG_ERROR,
        )

    if args.dry_run:
        audit.log_event(
            _EVT_ERASURE_COMPLETED,
            **request_fields,
            bytes_freed=0,
            files_modified=[],
        )
        _emit_purge_success(
            output_format,
            {
                "mode": "run",
                "kind": args.kind,
                "dry_run": True,
                "run_id": args.run_id,
                "would_delete": [str(p) for p in target_paths],
            },
        )
        return

    bytes_freed = 0
    files_modified: List[str] = []
    try:
        for path in target_paths:
            bytes_freed += _delete_path(path)
            files_modified.append(str(path))
    except OSError as exc:
        audit.log_event(
            _EVT_ERASURE_FAILED,
            **request_fields,
            error_class=exc.__class__.__name__,
            error_message=_sanitise_audit_error_message(str(exc)),
            files_modified=files_modified,
        )
        _output_error_and_exit(
            output_format,
            f"Deletion failed mid-batch: {exc}.  {len(files_modified)} of "
            f"{len(target_paths)} target(s) removed before failure.",
            EXIT_TRAINING_ERROR,
        )

    audit.log_event(
        _EVT_ERASURE_COMPLETED,
        **request_fields,
        bytes_freed=bytes_freed,
        files_modified=files_modified,
    )
    _emit_purge_success(
        output_format,
        {
            "mode": "run",
            "kind": args.kind,
            "dry_run": False,
            "run_id": args.run_id,
            "deleted": files_modified,
            "bytes_freed": bytes_freed,
        },
    )


def _staging_targets_for_run(base: Path, run_id: str) -> List[Path]:
    """Return existing ``final_model.staging`` paths for ``run_id``.

    Wave 2b final-review F-21-STAGING: only the Phase 9 v2 explicit form
    ``final_model.staging.<run_id>/`` is considered.  Earlier revisions
    also unconditionally included the legacy unscoped
    ``final_model.staging/`` candidate as a "pre-Phase-9-v2 backward
    compatibility" path, but that path could belong to any pre-v2 run
    — calling ``forgelm purge --run-id fg-X --kind staging`` would then
    silently delete fg-Y's staging dir if fg-Y was the actual occupant.
    For the unscoped path we now require an explicit ownership marker
    file ``staging_run_id`` (single line, exact ``run_id`` value) inside
    the directory; without it the legacy candidate is skipped.
    Operators with v0.5.0-or-earlier workspaces who genuinely want to
    purge the legacy ``final_model.staging/`` should either re-stage
    via the Phase 9 v2 path or remove the directory manually.
    """
    targets: List[Path] = []
    scoped = base / f"final_model.staging.{run_id}"
    if scoped.exists():
        targets.append(scoped)
    legacy = base / "final_model.staging"
    if legacy.exists() and _legacy_staging_owned_by(legacy, run_id):
        targets.append(legacy)
    return targets


def _legacy_staging_owned_by(legacy_dir: Path, run_id: str) -> bool:
    """Return ``True`` only when the legacy ``final_model.staging/`` carries
    a ``staging_run_id`` marker matching ``run_id``.

    The marker is a defence against silently deleting another run's
    staging directory.  Absent / unreadable / mismatching markers all
    cause the legacy path to be treated as "not owned by this run" and
    skipped.
    """
    marker = legacy_dir / "staging_run_id"
    if not marker.is_file():
        return False
    try:
        recorded = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return recorded == run_id


def _artefact_targets_for_run(base: Path, run_id: str) -> List[Path]:
    """Return ``compliance/`` files whose name embeds ``run_id`` as a token.

    Compliance bundle filenames embed the run-id; we accept both
    ``compliance_<run_id>.json`` and ``annex_iv_<run_id>.json``.  The
    token-boundary check (`_filename_contains_run_id`) defends against
    short-run-id false positives where a substring match would
    accidentally pick up files belonging to a different run.
    """
    compliance_dir = base / "compliance"
    if not compliance_dir.is_dir():
        return []
    return [
        compliance_dir / fname
        for fname in os.listdir(compliance_dir)
        if (compliance_dir / fname).is_file() and _filename_contains_run_id(fname, run_id)
    ]


# Per-kind dispatch: adding a new ``--kind`` value (post-Phase-21) is a
# one-row + one-helper edit instead of an if/elif chain extension.
_RUN_KIND_RESOLVERS: Dict[str, Any] = {
    "staging": _staging_targets_for_run,
    "artefacts": _artefact_targets_for_run,
}


def _resolve_run_kind_targets(output_dir: str, run_id: str, kind: str) -> List[Path]:
    """Return the on-disk paths the ``--kind`` flag refers to for ``run_id``.

    Dispatches to a per-kind resolver in :data:`_RUN_KIND_RESOLVERS`.
    Unknown kinds are caller error (argparse rejects them at parse
    time); we return an empty list defensively rather than raising so
    a typo on a new kind in the future surfaces as
    ``data.erasure_failed`` with `NoMatchingArtefacts` rather than an
    unhandled ``KeyError``.
    """
    resolver = _RUN_KIND_RESOLVERS.get(kind)
    if resolver is None:
        return []
    return resolver(Path(output_dir), run_id)


_RUN_ID_BOUNDARIES = ("_", "-", ".")


def _filename_contains_run_id(filename: str, run_id: str) -> bool:
    """Return True iff ``run_id`` appears in ``filename`` as a discrete
    token (flanked by recognised delimiters or string edges).

    Defends against the bare-substring failure mode where a short
    ``run_id`` accidentally matched longer file names that merely
    contained those characters.
    """
    if not run_id:
        return False
    idx = 0
    while True:
        found = filename.find(run_id, idx)
        if found == -1:
            return False
        before_ok = found == 0 or filename[found - 1] in _RUN_ID_BOUNDARIES
        end = found + len(run_id)
        after_ok = end == len(filename) or filename[end] in _RUN_ID_BOUNDARIES
        if before_ok and after_ok:
            return True
        idx = found + 1


def _path_inside_output_dir(target: Path, output_dir: str) -> bool:
    """Return ``True`` iff *target* resolves inside *output_dir*.

    Defence-in-depth boundary check for run-scoped erasure (F-P5-OPUS-02):
    ``--run-id`` is a free-form ``type=str`` argument with no charset
    constraint, so a value containing ``..`` segments can make
    :func:`_staging_targets_for_run` build a ``final_model.staging.<run_id>``
    path that ``os.path.normpath`` resolves *outside* ``output_dir`` — an
    arbitrary-directory ``shutil.rmtree``.  Mirrors
    :func:`forgelm.cli.subcommands._approve._staging_path_inside_output_dir`
    (realpath + commonpath) so both compliance paths share one boundary
    policy.  ``realpath`` follows symlinks, so a legitimate symlink whose
    target lives inside ``output_dir`` still validates — only paths that
    *escape* the boundary are rejected.
    """
    real_output = os.path.realpath(output_dir)
    real_target = os.path.realpath(str(target))
    try:
        return os.path.commonpath([real_output, real_target]) == real_output
    except ValueError:
        # commonpath raises ValueError when the paths live on different
        # drives (Windows) — treat as out-of-bounds.
        return False


def _delete_path(path: Path) -> int:
    """Remove a file or directory; return bytes freed."""
    if path.is_dir() and not path.is_symlink():
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        shutil.rmtree(path)
        return size
    size = path.stat().st_size
    path.unlink()
    return size


# ---------------------------------------------------------------------------
# Retention policy report (--check-policy)
# ---------------------------------------------------------------------------


def _run_purge_check_policy(args, output_format: str) -> None:
    """Handle ``forgelm purge --check-policy [--config <path>]``.

    Always exits 0 (per design §10 Q5: report-not-gate semantic).
    Operators wiring a CI gate use ``--output-format json`` and pipe
    to ``jq '.violations | length'`` themselves.

    Strict config loading: ``--check-policy`` is the one purge mode
    where the operator explicitly asked for a retention report.  A
    malformed YAML or a Pydantic validation error must surface as
    ``EXIT_CONFIG_ERROR`` rather than silently degrading to a
    "no retention block" notice (which the operator would mistake
    for "no violations").
    """
    import yaml as _yaml

    from forgelm.config import ConfigError

    try:
        config_loaded = _maybe_load_config(getattr(args, "config", None), strict=True)
    except (ConfigError, OSError, _yaml.YAMLError) as exc:
        # OSError covers FileNotFoundError + permission denied; ConfigError
        # wraps Pydantic ValidationError raised by ``load_config``;
        # ``yaml.YAMLError`` mirrors the inner catch tuple in
        # ``_maybe_load_config`` so a malformed YAML that bypasses the
        # ConfigError wrap still surfaces a formatted operator-facing
        # message instead of a stack trace. Any other class would be a
        # contract violation and should propagate.
        _output_error_and_exit(
            output_format,
            f"--check-policy could not load --config: {exc}",
            EXIT_CONFIG_ERROR,
        )
    if config_loaded is None or getattr(config_loaded, "retention", None) is None:
        msg = (
            "No `retention:` block in the loaded config; nothing to enforce.  "
            "See `docs/guides/gdpr_erasure.md` for the schema."
        )
        if output_format == "json":
            print(json.dumps({"success": True, "violations": [], "note": msg}))
        else:
            print(msg)
        sys.exit(EXIT_SUCCESS)

    output_dir = args.output_dir or "."
    violations = _scan_retention_violations(config_loaded.retention, output_dir)
    if output_format == "json":
        print(json.dumps({"success": True, "violations": violations, "count": len(violations)}, indent=2))
    else:
        if not violations:
            print(f"No retention-policy violations under {output_dir!r}.")
        else:
            print(f"Retention-policy violations under {output_dir!r} ({len(violations)}):")
            for v in violations:
                age_days = v.get("age_days", "?")
                horizon = v.get("horizon_days", "?")
                print(
                    f"  {v.get('artefact_kind', '?')}: {v.get('path', '?')} "
                    f"(age {age_days}d, horizon {horizon}d, age_source={v.get('age_source', '?')})"
                )
    sys.exit(EXIT_SUCCESS)


def _scan_retention_violations(retention, output_dir: str) -> List[Dict[str, Any]]:
    """Walk ``output_dir`` and report artefacts past their retention horizon.

    Belt-and-suspenders age resolution (design §3.3):

    1. **Belt** (preferred):  for run-scoped artefacts, the canonical
       age is the ``timestamp`` of the run's ``audit_log.jsonl`` genesis
       event (HMAC-signed when the operator opted in).
    2. **Suspenders**:  fall back to filesystem ``mtime`` and tag the
       violation with ``age_source="mtime"`` so an operator scanning
       the report knows the age signal is filesystem-derived.
    """
    violations: List[Dict[str, Any]] = []
    now = time.time()

    audit_log_path = os.path.join(output_dir, "audit_log.jsonl")
    # Wave 2b Round-5 review F-W2B-PURGE: build a per-run audit-age
    # lookup so each `final_model.staging.<run_id>/` (and each
    # `raw_documents/<run_id>/` when it exists) is aged from *its
    # own* first audit event rather than the merged-log genesis.
    # The genesis remains the fallback for artefacts that have no
    # owning run_id (audit_log itself, the legacy flat staging dir,
    # the compliance bundle, the data audit report).
    audit_ages = _build_audit_age_lookup(audit_log_path, now)

    # 4-tuples: (kind, path, horizon_days, run_id_or_None).  The
    # discovery helpers tag entries with the owning run_id so the
    # per-artefact age lookup hits the right audit timestamp.
    horizons: List[Tuple[str, str, int, Optional[str]]] = [
        ("audit_log", audit_log_path, retention.audit_log_retention_days, None),
        ("staging_dir", os.path.join(output_dir, "final_model.staging"), retention.staging_ttl_days, None),
        (
            "compliance_bundle",
            os.path.join(output_dir, "compliance"),
            retention.ephemeral_artefact_retention_days,
            None,
        ),
        (
            "data_audit_report",
            os.path.join(output_dir, "data_audit_report.json"),
            retention.ephemeral_artefact_retention_days,
            None,
        ),
    ]
    # Wave 2b Round-2 review: the canonical scan above missed the
    # per-run staging layout (`final_model.staging.<run_id>/` — what
    # the trainer actually creates since Phase 9 v2) and the raw
    # documents horizon.  Discover both at scan time so the report
    # reflects every artefact kind the retention block covers.
    horizons.extend(_discover_per_run_staging_horizons(output_dir, retention.staging_ttl_days))
    horizons.extend(_discover_raw_documents_horizons(output_dir, retention.raw_documents_retention_days))
    for kind, path, horizon_days, run_id in horizons:
        if horizon_days == 0 or not os.path.exists(path):
            continue
        age_seconds, age_source = _resolve_artefact_age(path, audit_ages, run_id, now)
        age_days = age_seconds / 86400.0
        if age_days > horizon_days:
            violations.append(
                {
                    "artefact_kind": kind,
                    "path": path,
                    "age_days": round(age_days, 1),
                    "horizon_days": horizon_days,
                    "age_source": age_source,
                }
            )
    return violations


def _parse_iso_timestamp_to_posix(ts: str) -> Optional[float]:
    """Parse an ISO-8601 timestamp string to POSIX seconds; ``None`` on failure.

    Extracted from :func:`_age_from_audit_log` so the parsing branches
    do not stack inside the file-walk loop.  Accepts both the ``Z``
    suffix shape AuditLogger emits and the ``+00:00`` form Python 3.10's
    ``datetime.fromisoformat`` requires.  Naive timestamps are treated
    as UTC (matches AuditLogger emission policy; defensive against
    external producers that drop the offset).
    """
    from datetime import datetime, timezone

    try:
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def _discover_per_run_staging_horizons(output_dir: str, horizon_days: int) -> List[Tuple[str, str, int, Optional[str]]]:
    """Enumerate ``final_model.staging.<run_id>/`` directories under
    ``output_dir`` so the retention scan reports each one separately.

    Returns ``[]`` when the horizon is disabled (``horizon_days == 0``)
    or the output dir cannot be listed (callers handle the empty
    iterable as "nothing to scan").

    Wave 2b Round-5 review F-W2B-PURGE: each tuple now carries the
    ``run_id`` extracted from the directory name so the caller can
    age each staging dir against *that run's* first audit event
    instead of the merged-log genesis.
    """
    if horizon_days == 0:
        return []
    discovered: List[Tuple[str, str, int, Optional[str]]] = []
    try:
        entries = os.listdir(output_dir)
    except OSError:
        return []
    for entry in entries:
        if not entry.startswith("final_model.staging."):
            continue
        full = os.path.join(output_dir, entry)
        if not os.path.isdir(full):
            continue
        run_id = entry[len("final_model.staging.") :]
        discovered.append((f"staging_dir[{run_id}]", full, horizon_days, run_id))
    return discovered


def _discover_raw_documents_horizons(output_dir: str, horizon_days: int) -> List[Tuple[str, str, int, Optional[str]]]:
    """Locate raw-documents directories ForgeLM ingest may have written.

    The retention block covers ``raw_documents_retention_days`` (Phase
    21 design §3 + GH-023 absorption); the canonical location is
    ``<output_dir>/raw_documents/`` (when ``forgelm ingest --output``
    points at the same dir).  We also recognise the legacy
    ``ingestion_output/`` from earlier templates.

    Raw-documents directories are not run-scoped (the ingest pipeline
    writes one shared corpus per output_dir), so the run_id slot is
    ``None`` and the caller falls back to the genesis age.
    """
    if horizon_days == 0:
        return []
    discovered: List[Tuple[str, str, int, Optional[str]]] = []
    for candidate in ("raw_documents", "ingestion_output"):
        path = os.path.join(output_dir, candidate)
        if os.path.exists(path):
            discovered.append((f"raw_documents[{candidate}]", path, horizon_days, None))
    return discovered


def _build_audit_age_lookup(audit_log_path: str, now: float) -> Dict[Optional[str], float]:
    """Walk the audit log once; return ``{None: genesis_age, run_id: age_for_run}``.

    Wave 2b Round-5 review F-W2B-PURGE: the previous
    ``_age_from_audit_log`` returned a single genesis age that the
    scanner reused for every artefact kind, so a `final_model.staging.
    <run_id>/` created weeks after the genesis event aged from the
    *wrong* timestamp.  This walk records the first POSIX timestamp
    per ``run_id`` (and the global genesis under the ``None`` key) so
    the per-artefact age is the right one.

    Cognitive complexity is kept under the SonarCloud S3776 ceiling
    by delegating per-line parsing to :func:`_parse_audit_event_age`.
    """
    out: Dict[Optional[str], float] = {}
    if not os.path.isfile(audit_log_path):
        return out
    try:
        with open(audit_log_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                _absorb_audit_line(raw, now, out)
    except OSError:
        return out
    return out


def _absorb_audit_line(raw: str, now: float, out: Dict[Optional[str], float]) -> None:
    """Parse one audit-log line; record genesis + per-run ages into ``out``.

    Skips blank, non-JSON, non-dict, and missing-timestamp lines.  Only
    the *first* timestamp per run_id (and the *first* timestamp overall
    for the genesis slot) is recorded — append-only invariant means
    earlier writes anchor the age.
    """
    line = raw.strip()
    if not line:
        return
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return
    if not isinstance(event, dict):
        return
    ts = event.get("timestamp")
    if not isinstance(ts, str):
        return
    posix = _parse_iso_timestamp_to_posix(ts)
    if posix is None:
        return
    age = now - posix
    if None not in out:
        out[None] = age
    run_id = event.get("run_id")
    if isinstance(run_id, str) and run_id and run_id not in out:
        out[run_id] = age


def _resolve_artefact_age(
    path: str,
    audit_ages: Dict[Optional[str], float],
    run_id: Optional[str],
    now: float,
) -> Tuple[float, str]:
    """Belt + suspenders age resolution; return ``(age_seconds, source)``.

    Resolution order:

    1. Per-run audit timestamp (when ``run_id`` matches a recorded run
       in the audit log) — the canonical signal for run-scoped
       artefacts like ``final_model.staging.<run_id>/``.
    2. Global genesis audit timestamp — the canonical signal for
       artefacts that are not run-scoped (audit_log itself, the
       compliance bundle, the data audit report).
    3. Filesystem ``mtime`` — last-resort fallback when the audit log
       is missing / unreadable / empty.
    """
    if run_id is not None and run_id in audit_ages:
        return audit_ages[run_id], "audit"
    genesis = audit_ages.get(None)
    if genesis is not None:
        return genesis, "audit"
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return 0.0, "mtime"
    return now - mtime, "mtime"


# ---------------------------------------------------------------------------
# Helpers + dispatcher entry
# ---------------------------------------------------------------------------


def _maybe_load_config(config_path: Optional[str], *, strict: bool = False):
    """Load the ``ForgeConfig`` from YAML.

    ``strict=False`` (default) is the best-effort mode used by the
    row-id / run-id erasure paths: those subcommands work without a
    config (the corpus-row erasure is intentionally config-agnostic),
    so a missing / unreadable / invalid YAML degrades silently to
    ``None``.

    ``strict=True`` is used by ``--check-policy`` where the operator
    is *explicitly* asking for a retention-policy report against the
    loaded config.  In that mode a malformed YAML or a schema error
    must surface as the original ``ConfigError`` / ``OSError`` so the
    operator sees the validation failure instead of a misleading
    "no `retention:` block" notice.
    """
    if not config_path:
        return None
    # F-XPR-02: narrow the catch tuple to the precise exception classes
    # ``load_config`` actually raises (``ConfigError`` for Pydantic /
    # YAML validation, ``OSError`` for I/O, ``yaml.YAMLError`` for parse
    # errors).  The previous bare ``except Exception`` was the exact
    # "BLE001 used to dodge thinking" example called out in
    # ``docs/standards/error-handling.md``.
    import yaml as _yaml

    from forgelm.config import ConfigError, load_config

    try:
        return load_config(config_path)
    except (ConfigError, OSError, _yaml.YAMLError) as exc:
        if strict:
            raise
        # Best-effort fallback: row-id / run-id paths run config-free.
        logger.debug("purge: could not load config %s: %s", config_path, exc)
        return None


def _render_row_success(payload: Dict[str, Any]) -> None:
    """Human-readable text rendering for ``--row-id`` mode."""
    if payload.get("dry_run"):
        print(
            f"[dry-run] Would erase {payload.get('matches')} row(s) starting at line "
            f"{payload.get('first_line')} (target_id_hash={(payload.get('row_id_hash') or 'unknown')[:16]}…, "
            f"salt_source={payload.get('salt_source')})."
        )
        return
    warns = payload.get("warnings") or []
    warn_str = f"; warnings: {', '.join(warns)}" if warns else ""
    print(f"Erased {payload.get('matches')} row(s); {payload.get('bytes_freed')} bytes freed{warn_str}.")


def _render_run_success(payload: Dict[str, Any]) -> None:
    """Human-readable text rendering for ``--run-id`` mode."""
    kind = payload.get("kind")
    run_id = payload.get("run_id")
    if payload.get("dry_run"):
        paths = payload.get("would_delete") or []
        print(f"[dry-run] Would delete {len(paths)} {kind} artefact(s) for run {run_id!r}:")
        for p in paths:
            print(f"  - {p}")
        return
    deleted = payload.get("deleted") or []
    print(f"Deleted {len(deleted)} {kind} artefact(s) for run {run_id!r}; {payload.get('bytes_freed')} bytes freed.")


# Per-mode text-renderer dispatch table.  Adding a new ``forgelm purge``
# mode is a one-row edit + one new ``_render_*_success`` helper.
_TEXT_RENDERERS: Dict[str, Any] = {
    "row": _render_row_success,
    "run": _render_run_success,
}


def _emit_purge_success(output_format: str, payload: Dict[str, Any]) -> None:
    """Emit the success envelope for a purge subcommand.

    JSON output is shape-stable across modes; text output dispatches to
    the per-mode renderer in :data:`_TEXT_RENDERERS`.  Cognitive
    complexity stayed at S3776 ceiling (15) by replacing the nested
    if/else chain with a dict lookup.
    """
    if output_format == "json":
        print(json.dumps({"success": True, **payload}, indent=2))
        return
    renderer = _TEXT_RENDERERS.get(payload.get("mode", "?"))
    if renderer is not None:
        renderer(payload)


def _run_purge_cmd(args, output_format: str) -> None:
    """Top-level dispatcher for ``forgelm purge``.

    Mode resolution:

    - ``--check-policy`` is a standalone flag; no other purge mode
      may be combined with it (argparse enforces, defensive check
      below).
    - ``--row-id`` requires ``--corpus``.
    - ``--run-id`` requires ``--kind``.
    """
    if getattr(args, "check_policy", False):
        if args.row_id or args.run_id:
            _output_error_and_exit(
                output_format,
                "--check-policy is mutually exclusive with --row-id / --run-id.",
                EXIT_CONFIG_ERROR,
            )
        _run_purge_check_policy(args, output_format)
        return
    if args.row_id and args.run_id:
        _output_error_and_exit(
            output_format,
            "--row-id and --run-id are mutually exclusive (different erasure scopes).",
            EXIT_CONFIG_ERROR,
        )
    if args.row_id:
        _run_purge_row_id(args, output_format)
        sys.exit(EXIT_SUCCESS)
    if args.run_id:
        _run_purge_run_id(args, output_format)
        sys.exit(EXIT_SUCCESS)
    _output_error_and_exit(
        output_format,
        "forgelm purge: one of --row-id <id> --corpus <path> / --run-id <id> --kind {staging,artefacts} / --check-policy is required.",
        EXIT_CONFIG_ERROR,
    )


__all__ = [
    "_run_purge_cmd",
    "_run_purge_row_id",
    "_run_purge_run_id",
    "_run_purge_check_policy",
    "_resolve_salt",
    "_hash_target_id",
    "_find_matching_rows",
    "_atomic_rewrite_dropping_lines",
    "_detect_warning_conditions",
    "_scan_retention_violations",
]
