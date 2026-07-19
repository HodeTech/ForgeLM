"""EU AI Act compliance, training data provenance, and audit trail generation.

Covers: Article 9 (Risk Management), Article 10 (Data Governance),
Article 11 + Annex IV (Technical Documentation), Article 12 (Record-Keeping),
Article 13 (Transparency/Deployer Instructions), Article 14 (Human Oversight),
Article 15 (Model Integrity).
"""

import concurrent.futures
import getpass
import hashlib
import hmac as _hmac_module
import json
import logging
import os
import socket
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ._version import __version__ as _forgelm_version
from .config import ConfigError, WebhookConfig

# Webhook fields persisted into the compliance manifest so the post-training
# ``forgelm approve`` / ``forgelm reject`` dispatchers (which run with only
# ``--output-dir``, no ``--config``) can re-resolve a WebhookNotifier from the
# co-located JSON.  Derived from the live ``WebhookConfig`` schema so the
# persisted shape can NEVER drift from the model — the drift that previously
# persisted six non-existent fields, dropped the real ones, and made the
# rebuilt SimpleNamespace miss attributes the notifier reads (crashing
# ``approve`` with AttributeError *after* the model was already promoted).
#
# ``url`` is deliberately excluded: it can carry a Slack/Teams secret embedded
# in the URL path, and this manifest is a plain-JSON artefact typically
# committed to the auditor's evidence bundle.  Operators use the env-backed
# ``url_env`` indirection for the secret, so the persisted shape still
# re-resolves the webhook at approve/reject time without leaking the credential.
_WEBHOOK_SECRET_FIELDS = frozenset({"url"})
_WEBHOOK_PERSIST_FIELDS = tuple(name for name in WebhookConfig.model_fields if name not in _WEBHOOK_SECRET_FIELDS)

# Stable machine token prefixed onto the OSError-shaped pipeline-manifest
# violation so the CLI's exit-code routing keys off an unambiguous marker
# rather than the free-text substring ``unreadable`` (which a future reworded
# violation could accidentally contain, silently flipping exit 1 → exit 2).
# The CLI matches this exact prefix; keep the two in lockstep (F-P4-OPUS-25).
PIPELINE_MANIFEST_IO_ERROR_PREFIX = "IO_ERROR::"

# Sibling routing token for the *pre-flight input* failures — a manifest
# file that is absent, or present but unparseable as JSON.  Neither says
# anything about the pipeline's chain integrity: the verifier never got to
# look at a payload.  Tagging them lets the CLI keep those on
# ``EXIT_CONFIG_ERROR`` (1) while every remaining violation — structural,
# chain-integrity, missing per-stage evidence — routes to
# ``EXIT_INTEGRITY_FAILURE`` (6).  Same discipline as the IO token above:
# match the prefix, never the free text.
PIPELINE_MANIFEST_INPUT_ERROR_PREFIX = "INPUT_ERROR::"

# Recommended minimum length for ``FORGELM_AUDIT_SECRET``.  Shorter secrets are
# accepted (no hard-fail) but trigger a one-time weak-secret WARNING because the
# audit HMAC key's entropy is bounded by the secret's (F-P5-OPUS-13).
_MIN_AUDIT_SECRET_LEN = 16

# flock is Unix-only; on Windows there is NO cross-process lock — the helpers
# below are no-ops (no lock acquired). Do not share an output_dir across
# concurrent processes on Windows; use a distinct output_dir per run.
try:
    import fcntl as _fcntl

    def _flock_ex(f) -> None:
        _fcntl.flock(f, _fcntl.LOCK_EX)

    def _flock_un(f) -> None:
        _fcntl.flock(f, _fcntl.LOCK_UN)

except ImportError:  # pragma: no cover — Windows path

    def _flock_ex(f) -> None:  # type: ignore[misc]
        pass

    def _flock_un(f) -> None:  # type: ignore[misc]
        pass


logger = logging.getLogger("forgelm.compliance")


# ---------------------------------------------------------------------------
# Art. 12: Structured Audit Event Log
# ---------------------------------------------------------------------------


class AuditLogger:
    """Append-only JSON Lines audit log for EU AI Act Art. 12 record-keeping."""

    def __init__(self, output_dir: str, run_id: Optional[str] = None):
        self.run_id = run_id or f"fg-{uuid.uuid4().hex[:12]}"
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.log_path = os.path.join(output_dir, "audit_log.jsonl")
        self._manifest_path = self.log_path + ".manifest.json"
        # Article 12 record-keeping requires a real operator on every entry.
        # The previous fallback chain ``$FORGELM_OPERATOR -> $USER -> "unknown"``
        # silently produced ``operator="unknown"`` when both env vars were
        # missing (CI runners, container images with no login user). That made
        # the audit log unattributable — a regulator cannot identify who ran
        # the job. New policy:
        #
        # 1. If ``FORGELM_OPERATOR`` is set, use it verbatim (CI / pipelines
        #    pin a deliberate identity here).
        # 2. Otherwise derive ``<getpass.getuser()>@<socket.gethostname()>`` —
        #    matches how Unix audit subsystems attribute work.
        # 3. If no username can be resolved, refuse to start unless the
        #    operator opts in to anonymous logging via
        #    ``FORGELM_ALLOW_ANONYMOUS_OPERATOR=1``. Loud failure beats a
        #    silent ``"unknown"`` smear across the chain.
        operator_env = os.getenv("FORGELM_OPERATOR")
        if operator_env:
            self.operator = operator_env
        else:
            try:
                username = getpass.getuser()
            except (OSError, KeyError, ImportError):
                # ``getpass.getuser()`` fails on systems where neither
                # ``LOGNAME``/``USER``/``LNAME``/``USERNAME`` env vars nor the
                # ``pwd`` lookup resolves an identity. Its failure surface is
                # wider than ``OSError`` alone:
                #   - ``KeyError``: the current UID has no ``/etc/passwd``
                #     entry, so ``pwd.getpwuid(os.getuid())`` raises — the
                #     arbitrary-numeric-UID container case (``docker run
                #     --user 12345``, OpenShift's random-UID policy) this
                #     fallback exists to handle.
                #   - ``ImportError``: Windows has no ``pwd`` module, so
                #     ``getpass.getuser()`` raises ``ModuleNotFoundError``
                #     when ``USERNAME`` is unset.
                #   - ``OSError``: other identity-resolution failures.
                # We still honour the explicit opt-in below; fall through
                # with no username.
                username = None
            hostname = socket.gethostname() or "unknown-host"
            if username:
                self.operator = f"{username}@{hostname}"
            else:
                allow_anonymous = os.getenv("FORGELM_ALLOW_ANONYMOUS_OPERATOR") == "1"
                if not allow_anonymous:
                    raise ConfigError(
                        "Operator identity unavailable: no FORGELM_OPERATOR set, "
                        "and getpass.getuser() could not resolve a username. "
                        "Set FORGELM_OPERATOR=<id> for CI/CD pipelines, or "
                        "FORGELM_ALLOW_ANONYMOUS_OPERATOR=1 to opt in to "
                        "anonymous audit entries (not recommended for "
                        "EU AI Act Article 12 record-keeping)."
                    )
                self.operator = f"anonymous@{hostname}"
        # Per-run HMAC key: SHA-256(operator_secret || run_id).  The secret is
        # required for tamper-evident HMACs because ``run_id`` is part of the
        # public log header — without a non-empty secret an attacker who can
        # rewrite the file knows the key and could re-sign forged entries.
        # When the secret is missing we therefore disable HMAC emission
        # entirely (the SHA-256 hash chain is still written; only the
        # per-line authenticator drops out) so we never claim
        # tamper-evidence we cannot deliver.
        raw_secret = os.getenv("FORGELM_AUDIT_SECRET", "")
        if raw_secret:
            # The HMAC key's entropy is bounded by the secret's, so a short,
            # low-entropy secret makes the per-line ``_hmac`` tag we sell as
            # "tamper-evident" brute-forceable.  We don't hard-fail (that would
            # break deployments already running with a short secret on the
            # mainline audit path), but we surface a one-time WARNING so the
            # weak-secret risk is visible (F-P5-OPUS-13).  ForgeLM is "not a
            # key-management system" — 32+ random bytes from a KMS is the
            # documented recommendation (docs/design/iso27001_soc2_alignment.md).
            if len(raw_secret) < _MIN_AUDIT_SECRET_LEN:
                logger.warning(
                    "FORGELM_AUDIT_SECRET is %d characters — shorter than the "
                    "accepted minimum of %d. A low-entropy secret makes the "
                    "per-line audit HMAC forgeable; use at least %d characters "
                    "(32+ random bytes from a KMS is recommended for production).",
                    len(raw_secret),
                    _MIN_AUDIT_SECRET_LEN,
                    _MIN_AUDIT_SECRET_LEN,
                )
            self._hmac_key: Optional[bytes] = hashlib.sha256(raw_secret.encode() + self.run_id.encode()).digest()
        else:
            self._hmac_key = None
        self._prev_hash = self._load_last_hash()

    def _read_chain_head(self, fh) -> str:
        """Compute the SHA-256 of the last newline-terminated entry in *fh*.

        Pure helper that operates on an already-open binary file handle. Used
        by both :meth:`_load_last_hash` (init path, opens its own handle) and
        :meth:`log_event` (write path, re-reads under the same flock to
        defeat the multi-writer fork race documented in the class docstring).

        Returns ``"genesis"`` when the file is empty.

        Raises ``ValueError`` when the file does not end with a newline,
        because :meth:`log_event` always writes ``entry_json + "\\n"`` —
        the only way to land on an unterminated last record is a crash
        mid-write or external corruption, in which case hashing the
        partial body would silently anchor the chain to a truncated
        entry.
        """
        fh.seek(0, 2)
        size = fh.tell()
        if size == 0:
            return "genesis"
        # Trailing-newline guard. Read the final byte cheaply and refuse
        # to derive a chain head from an unterminated tail (it would be
        # a truncated record).
        fh.seek(size - 1)
        if fh.read(1) != b"\n":
            raise ValueError(
                f"Audit log {self.log_path!r} does not end with a newline — "
                "the final record is truncated. Refusing to silently re-root "
                "the hash chain on a partial entry; investigate or repair the "
                "log before resuming."
            )

        # Progressive-widen tail read.
        #
        # Start at a 4 KiB tail (typical audit entry < 1 KiB so this hits in
        # one read for the common case). When the tail starts mid-record
        # (seek-landed inside an entry > tail size) ``readline()`` consumes
        # the partial first line, leaving an empty whole-records segment;
        # we then **double the window** and retry, up to the full file.
        # This guarantees we never hash a truncated record — the prior
        # 4 KiB-only fallback would silently re-root to a partial entry
        # when a single record exceeded 4 KiB.
        window = 4096
        while True:
            seek_start = max(0, size - window)
            fh.seek(seek_start)
            if seek_start > 0:
                fh.readline()  # drop partial first line
            tail = fh.read()
            lines = self._decode_lines(tail)
            if lines:
                return hashlib.sha256(lines[-1].encode("utf-8")).hexdigest()
            if seek_start == 0:
                # We read the entire file and got no whole record — the
                # log starts mid-record (impossible for a valid file with
                # the trailing-newline guard above). Treat as fresh log.
                return "genesis"
            window *= 2

    def _decode_lines(self, blob: bytes) -> list:
        """UTF-8 decode + split into non-empty stripped lines, or raise."""
        try:
            return [ln for ln in blob.decode("utf-8").splitlines() if ln.strip()]
        except UnicodeDecodeError as e:
            raise ValueError(
                f"Audit log {self.log_path!r} contains non-UTF-8 data — likely corrupt: {e}. "
                "Refusing to silently re-root the hash chain."
            ) from e

    def _load_last_hash(self) -> str:
        """Read the last line hash from an existing log file to restore chain continuity.

        Distinguishes "no file" (legitimate first run, returns ``"genesis"``)
        from "file exists but unreadable" (filesystem error or corrupt log,
        raises ``OSError``). The previous version swallowed any exception
        with ``logger.debug`` and silently re-rooted the chain — invisible
        at default INFO log level, undetectable downstream.
        """
        if not os.path.isfile(self.log_path):
            return "genesis"
        try:
            with open(self.log_path, "rb") as f:
                return self._read_chain_head(f)
        except OSError as e:
            # Real I/O failure — surface loudly. A silent re-root would
            # break the Article 12 record-keeping contract: a downstream
            # verifier cannot tell a missing chain head from a corrupt one.
            raise OSError(
                f"Audit log exists at {self.log_path!r} but could not be read: {e}. "
                "Refusing to silently re-root the hash chain."
            ) from e

    def _check_genesis_manifest(self) -> bool:
        """Refuse to re-root the chain if the manifest pins a truncated-away log.

        An attacker who can write to the audit directory can delete the JSONL
        and start a new chain; they cannot also forge the manifest (written
        once on first entry, never overwritten) without detection.

        This is the **sole write-time** truncation guard (``verify_audit_log``
        is the strong verify-time gate). Two conditions fail closed here, each
        logging an ``AUDIT INTEGRITY`` ERROR and raising ``ConfigError`` so the
        re-root is refused at the moment it occurs (mirroring the loud-fail
        operator-identity policy in ``__init__``):

        1. The manifest is present but **unreadable/corrupt** — the chain can
           no longer be verified, so corrupting the manifest must not be a
           quieter path to disarming the guard than deleting the log.
        2. The manifest pins a real first entry but the **log is absent/empty**
           — the next write would silently re-root the chain on disk.

        An operator who deliberately rotated/cleared the log (or accepts a
        corrupt manifest) can opt in to the re-root via
        ``FORGELM_ALLOW_AUDIT_REROOT=1`` (the ERROR still fires).

        Returns ``True`` when a present manifest was overridden by the opt-in
        re-root — the caller MUST then regenerate it via
        ``_write_genesis_manifest(..., force=True)`` so the fresh chain's
        genesis entry gets pinned and ``verify_audit_log`` succeeds again
        (leaving the stale/corrupt manifest in place would keep the re-rooted
        chain permanently unverifiable). Returns ``False`` for a clean first
        run with no manifest.
        """
        if not os.path.isfile(self._manifest_path):
            return False
        try:
            with open(self._manifest_path, "r", encoding="utf-8") as fh:
                manifest = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            # A present-but-unreadable manifest fails closed, matching the
            # verify-time gate (``_verify_genesis_manifest``) which marks the
            # identical situation ``valid=False``. Warning-and-returning here
            # would let the next append silently re-root the chain — exactly
            # the truncation this guard exists to detect, reachable by
            # corrupting the manifest instead of deleting the log. The same
            # ``FORGELM_ALLOW_AUDIT_REROOT`` opt-in as the absent/empty-log
            # branch lets a deliberate operator proceed (the ERROR still fires).
            logger.error(
                "AUDIT INTEGRITY: genesis manifest at %s is present but unreadable (%s). "
                "The chain cannot be verified and would be silently re-rooted.",
                self._manifest_path,
                exc,
            )
            if os.getenv("FORGELM_ALLOW_AUDIT_REROOT") != "1":
                raise ConfigError(
                    f"Audit log re-root refused: genesis manifest at {self._manifest_path!r} "
                    f"is present but unreadable ({exc}) — the chain cannot be verified and "
                    "would be silently re-rooted. Investigate or repair the manifest, or set "
                    "FORGELM_ALLOW_AUDIT_REROOT=1 to deliberately start a fresh chain "
                    "(not recommended for EU AI Act Article 12 record-keeping)."
                ) from exc
            # Opt-in permitted the re-root: the corrupt manifest must be
            # regenerated for the fresh chain, else verify_audit_log stays
            # permanently broken with "manifest present but unreadable".
            return True
        if not os.path.isfile(self.log_path) or os.path.getsize(self.log_path) == 0:
            expected = manifest.get("first_entry_sha256", "unknown")
            logger.error(
                "AUDIT INTEGRITY: genesis manifest exists at %s but audit log is absent or empty. "
                "The log may have been truncated. First-entry hash expected: %s",
                self._manifest_path,
                expected,
            )
            if os.getenv("FORGELM_ALLOW_AUDIT_REROOT") != "1":
                raise ConfigError(
                    f"Audit log re-root refused: genesis manifest at {self._manifest_path!r} "
                    f"pins first-entry hash {expected} but the log is absent or empty — "
                    "the chain would be silently re-rooted (a truncation the manifest exists "
                    "to detect). Investigate or repair the log, or set "
                    "FORGELM_ALLOW_AUDIT_REROOT=1 to deliberately start a fresh chain "
                    "(not recommended for EU AI Act Article 12 record-keeping)."
                )
            # Opt-in permitted the re-root: the stale manifest pins a chain
            # that no longer exists on disk, so it must be regenerated for the
            # fresh chain, else verify_audit_log stays permanently broken with
            # "manifest mismatch".
            return True
        return False

    def _write_genesis_manifest(self, first_entry_sha256: str, force: bool = False) -> None:
        """Pin the first-ever entry hash so log truncation is detectable.

        Written exactly once for a fresh chain (when the manifest file does not
        yet exist). The sole exception is an audited break-glass re-root
        (``FORGELM_ALLOW_AUDIT_REROOT=1``, surfaced by
        :meth:`_check_genesis_manifest` returning ``True``), which passes
        ``force=True`` so the stale/corrupt manifest is atomically replaced
        with a pin for the fresh chain's genesis entry. Without this the
        re-rooted chain would stay permanently unverifiable.
        """
        if os.path.isfile(self._manifest_path) and not force:
            return
        manifest = {
            "audit_log": os.path.basename(self.log_path),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "first_entry_sha256": first_entry_sha256,
        }
        # Atomic write (tmp + fsync + os.replace), matching export_pipeline_manifest
        # and log_event's fsync discipline. A crash mid-write (power loss, OOM-kill)
        # must never leave a truncated manifest — a corrupt manifest disarms the
        # write-time re-root guard just as effectively as deleting the log.
        tmp_path = self._manifest_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self._manifest_path)
            # Also fsync the parent directory so the rename's *directory
            # entry* is durable, not just the file contents — mirrors
            # ``_purge.py::_atomic_rewrite_dropping_lines``. Without this,
            # a crash between the rename and the directory metadata
            # flush can drop the manifest on non-journaled filesystems;
            # since the manifest is genesis-only (write-once), a lost
            # rename here permanently disarms truncation-detection for
            # this log with no error surfaced on the next run.
            # ``O_DIRECTORY`` is unsupported on Windows; trap and continue.
            manifest_dir = os.path.dirname(self._manifest_path) or "."
            try:
                dir_fd = os.open(manifest_dir, os.O_DIRECTORY)
            except (AttributeError, OSError):  # pragma: no cover — Windows / unusual FS
                pass
            else:
                try:
                    os.fsync(dir_fd)
                except OSError as exc:
                    # The manifest file itself is already durably written and
                    # atomically in place (fsync + os.replace above); only the
                    # parent-directory-entry fsync — which protects solely
                    # against a crash in the narrow window right after the
                    # rename — failed. Do NOT let this fall into the generic
                    # "could not write genesis manifest" warning below: the
                    # manifest is present and valid, and an operator reading
                    # that message during an audit would wrongly conclude the
                    # pin is missing/corrupt.
                    logger.warning(
                        "Genesis manifest written to %s but parent-directory fsync failed (%s) — "
                        "durability not guaranteed if the host crashes in the window right after "
                        "the rename.",
                        self._manifest_path,
                        exc,
                    )
                finally:
                    os.close(dir_fd)
        except OSError as exc:
            logger.warning("Could not write genesis manifest to %s: %s", self._manifest_path, exc)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def log_event(self, event: str, **details) -> None:
        """Append a tamper-evident structured event to the audit log.

        Each entry includes the SHA-256 hash of the previous entry,
        creating a hash chain that detects modifications or deletions.

        Hardening:

        - **flock**: ``LOCK_EX`` around the write prevents interleaved
          lines from concurrent trainers sharing the same output directory.
          The chain head is re-read from disk *under the lock* so two
          writers sharing the same log file cannot both append against a
          stale ``self._prev_hash`` (which would silently fork the chain).
        - **HMAC**: when ``FORGELM_AUDIT_SECRET`` is set, each line carries
          ``_hmac`` — SHA-256(HMAC-key, line_without_hmac) where the key is
          derived from ``run_id`` + the secret. Without a secret the field
          is omitted entirely (a key derived solely from the public
          ``run_id`` would be forgeable, so we don't claim authentication
          we cannot deliver). The SHA-256 chain still detects modification.
        - **Post-write hash advancement**: ``self._prev_hash`` is updated
          only after the line lands on disk so a write failure leaves the
          chain intact for a retry.
        - **Genesis manifest**: on the first write to a new log, pins the
          first-entry hash in a sidecar file so log truncation is detectable.
        """
        reroot_permitted = False
        if self._prev_hash == "genesis":
            reroot_permitted = self._check_genesis_manifest()

        try:
            # Open in "a+" so we can both read the existing tail (under
            # lock) and append to the same handle.
            with open(self.log_path, "a+b") as f:
                _flock_ex(f)
                try:
                    prev_hash = self._read_chain_head(f)
                    is_genesis = prev_hash == "genesis"

                    entry = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "run_id": self.run_id,
                        "operator": self.operator,
                        "event": event,
                        "prev_hash": prev_hash,
                        **details,
                    }
                    # Compute HMAC over the entry without the _hmac tag so
                    # the tag can be stripped before verification without
                    # invalidating the hash chain. Skip when no secret is
                    # configured — see class docstring.
                    if self._hmac_key is not None:
                        entry_json_for_hmac = json.dumps(entry, default=str)
                        entry["_hmac"] = _hmac_module.new(
                            self._hmac_key,
                            entry_json_for_hmac.encode(),
                            hashlib.sha256,
                        ).hexdigest()
                    entry_json = json.dumps(entry, default=str)

                    f.seek(0, 2)
                    f.write((entry_json + "\n").encode("utf-8"))
                    f.flush()
                    # ``flush()`` only pushes user-space buffers into the OS
                    # kernel; an unclean shutdown (power loss, kernel panic,
                    # OOM-kill of the container host) before the kernel
                    # write-back can still drop the entry. ``fsync`` blocks
                    # until the write reaches stable storage, so the
                    # ``self._prev_hash`` advance below is durable. The cost
                    # (one fsync per audit event, typically a handful per
                    # training run) is negligible next to the cost of losing
                    # a record-keeping line.
                    os.fsync(f.fileno())
                    new_hash = hashlib.sha256(entry_json.encode()).hexdigest()
                finally:
                    _flock_un(f)
        except OSError as e:
            # Article 12 record-keeping is a load-bearing artefact; a write
            # failure must surface to the caller, not be quietly swallowed.
            raise OSError(
                f"Failed to write audit event {event!r} to {self.log_path!r}: {e}. "
                "The hash chain has NOT been advanced — retry or fail the run."
            ) from e
        if is_genesis:
            self._write_genesis_manifest(new_hash, force=reroot_permitted)
        self._prev_hash = new_hash


# ---------------------------------------------------------------------------
# Art. 10: Data Governance & Quality Report
# ---------------------------------------------------------------------------


def _build_text_length_stats(split_data: Any, split_name: str) -> Optional[Dict[str, Any]]:
    """Compute min/max/mean/median/p95 of the ``text`` column, if present."""
    if not (hasattr(split_data, "column_names") and "text" in split_data.column_names):
        return None
    try:
        texts = split_data["text"]
        lengths = sorted(len(t) for t in texts if isinstance(t, str))
    except (KeyError, ValueError, TypeError, OSError, IndexError) as exc:
        # KeyError: column dropped between the membership check and access.
        # OSError: HF Datasets lazy-load failure on Arrow / Parquet shard.
        # ValueError/TypeError: column dtype not coercible into Python str
        # iteration (e.g., binary blobs, nested struct columns). Stats are
        # advisory — return None and let the caller record an empty entry.
        logger.debug("Could not compute text stats for %s: %s", split_name, exc)
        return None
    if not lengths:
        return None
    return {
        "min": lengths[0],
        "max": lengths[-1],
        "mean": round(sum(lengths) / len(lengths), 1),
        "median": lengths[len(lengths) // 2],
        "p95": lengths[int(len(lengths) * 0.95)],
    }


def _build_split_info(split_name: str, split_data: Any) -> Dict[str, Any]:
    """Per-split sample count + column schema + length distribution."""
    info: Dict[str, Any] = {"sample_count": len(split_data)}
    if hasattr(split_data, "column_names"):
        info["columns"] = split_data.column_names
    text_length = _build_text_length_stats(split_data, split_name)
    if text_length:
        info["text_length"] = text_length
    return info


def _governance_section(config: Any) -> Optional[Dict[str, Any]]:
    """Return the operator-supplied Article 10 metadata block, if any."""
    gov_cfg = getattr(config.data, "governance", None)
    if not gov_cfg:
        return None
    return {
        "collection_method": gov_cfg.collection_method,
        "annotation_process": gov_cfg.annotation_process,
        "known_biases": gov_cfg.known_biases,
        "personal_data_included": gov_cfg.personal_data_included,
        "dpia_completed": gov_cfg.dpia_completed,
    }


def _maybe_inline_audit_report(config: Any) -> Optional[Dict[str, Any]]:
    """Read ``data_audit_report.json`` from ``training.output_dir`` if it's there.

    Loud-but-non-fatal hint when the file is missing: the audit CLI
    defaults to ``./audit/`` whereas the trainer's output_dir is
    typically ``./checkpoints/`` — without explicit alignment the
    inlining silently no-ops and the governance bundle ships without
    the Article 10 data-quality section.
    """
    output_dir = getattr(getattr(config, "training", None), "output_dir", None)
    if not output_dir:
        return None
    audit_path = os.path.join(output_dir, "data_audit_report.json")
    if not os.path.isfile(audit_path):
        # Wave 3 / Faz 28 (F-compliance-111): escalated from INFO to
        # WARNING.  A missing data_audit_report.json is a real Article
        # 10 compliance gap — the governance bundle ships without its
        # data-quality section, which is exactly the surface a regulator
        # would inspect first.  Operators reading INFO-level logs out
        # of habit miss the signal; WARNING is the documented level for
        # "nothing crashed but something compliance-relevant degraded."
        logger.warning(
            "No data_audit_report.json at %s — governance report will lack the "
            "Article 10 data-quality section. Run "
            "`forgelm audit <dataset> --output %s` before training to populate it.",
            audit_path,
            output_dir,
        )
        return None
    try:
        with open(audit_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        # Audit JSON is best-effort enrichment — corrupt UTF-8 or a
        # malformed file must not abort governance report generation.
        logger.warning("Could not inline data_audit_report.json (%s): %s", audit_path, exc)
        return None


def generate_data_governance_report(config: Any, dataset: Dict[str, Any]) -> Dict[str, Any]:
    """Generate data quality and governance report per EU AI Act Article 10.

    When an audit report (``data_audit_report.json``) was produced by
    ``forgelm audit`` and lives in the trainer's checkpoint dir,
    its findings are inlined under the ``data_audit`` key so the governance
    artifact is a single self-contained document rather than a pointer.

    ``report["data_audit_inlined"]`` is always set to a bool so the caller can
    record in the append-only audit log whether the Article 10 data-quality
    section made it into the bundle (F-P4-OPUS-23) rather than relying on the
    transient WARNING that ``_maybe_inline_audit_report`` emits.
    """
    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "primary_dataset": config.data.dataset_name_or_path,
        "splits": {name: _build_split_info(name, data) for name, data in dataset.items()},
    }

    governance = _governance_section(config)
    if governance:
        report["governance"] = governance

    audit = _maybe_inline_audit_report(config)
    report["data_audit_inlined"] = audit is not None
    if audit is not None:
        report["data_audit"] = audit

    return report


# ---------------------------------------------------------------------------
# Art. 15: Model Integrity Verification
# ---------------------------------------------------------------------------


def hash_file(filepath: str, rel_path: str) -> dict:
    """Hash one artifact for the Article 15 integrity manifest.

    Public (non-underscore) because it is a stable cross-module helper: both
    :func:`generate_model_integrity` here and ``forgelm verify-integrity``
    (``cli/subcommands/_verify_integrity.verify_integrity``) depend on producing
    byte-identical ``{file, sha256, size_bytes}`` records, so a signature change
    must stay in lock-step across both (F-M-10)."""
    sha256 = hashlib.sha256()
    size = 0
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
            size += len(chunk)
    return {"file": rel_path, "sha256": sha256.hexdigest(), "size_bytes": size}


def generate_model_integrity(final_path: str) -> Dict[str, Any]:
    """Compute SHA-256 checksums of all output model artifacts.

    F-P5-OPUS-08: ``os.walk`` with the default ``followlinks=False``
    correctly refuses to recurse *into* symlinked directories, but a
    symlinked *file* inside the tree is still listed and ``open()``
    transparently follows it to an out-of-tree target — so the Article 15
    integrity bundle would attribute (and hash the contents of) an
    external file as a model artifact.  Each file's realpath is bounded to
    the model tree via ``commonpath``; any file whose real target escapes
    is skipped and recorded under ``skipped_symlinks`` so the omission is
    auditable rather than silent.
    """
    integrity: Dict[str, Any] = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "model_path": final_path,
        "artifacts": [],
    }

    if not os.path.isdir(final_path):
        return integrity

    base = os.path.realpath(final_path)
    file_pairs = []
    skipped_symlinks: List[str] = []
    # followlinks=False is the default; named explicitly so the symlinked-
    # directory safety is not a silent assumption a refactor could drop.
    for root, _dirs, files in os.walk(final_path, followlinks=False):
        for filename in sorted(files):
            filepath = os.path.join(root, filename)
            # Normalise separators so a Windows-generated manifest records
            # POSIX-style "subdir/file" rather than "subdir\\file"; the
            # verifier compares this against an os.relpath on disk that it
            # normalises the same way, keeping the manifest cross-platform.
            rel_path = os.path.relpath(filepath, final_path).replace(os.sep, "/")
            real = os.path.realpath(filepath)
            try:
                contained = os.path.commonpath([real, base]) == base
            except ValueError:
                # Different drives (Windows) → cannot share a common path;
                # treat as escaping so an out-of-tree target is never hashed.
                contained = False
            if not contained:
                skipped_symlinks.append(rel_path)
                continue
            file_pairs.append((filepath, rel_path))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(hash_file, fp, rp) for fp, rp in file_pairs]
        # as_completed yields in completion order (non-deterministic); the
        # explicit sort below restores a stable, diff-friendly artifact list.
        integrity["artifacts"] = [f.result() for f in concurrent.futures.as_completed(futures)]

    integrity["artifacts"].sort(key=lambda x: x["file"])
    if skipped_symlinks:
        integrity["skipped_symlinks"] = sorted(skipped_symlinks)

    return integrity


# ---------------------------------------------------------------------------
# Data Provenance (existing, unchanged)
# ---------------------------------------------------------------------------


def _fingerprint_local_file(dataset_path: str, fingerprint: Dict[str, Any]) -> None:
    """Populate ``fingerprint`` with size/mtime/sha256 of a local file.

    Symlinks are resolved before hashing; ``stat`` is captured from the
    same open fd as the SHA-256 stream so a concurrent writer surfaces as
    an inconsistent fingerprint rather than a silent partial read.
    """
    resolved = os.path.realpath(dataset_path)
    if resolved != dataset_path:
        fingerprint["resolved_path"] = resolved

    sha256 = hashlib.sha256()
    with open(resolved, "rb") as f:
        stat = os.fstat(f.fileno())
        fingerprint["size_bytes"] = stat.st_size
        fingerprint["modified"] = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    fingerprint["sha256"] = sha256.hexdigest()


def _fingerprint_hf_metadata(dataset_path: str, fingerprint: Dict[str, Any]) -> None:
    """Populate ``fingerprint`` with HF Hub builder metadata (version, description, size).

    Best-effort: catches realistic ``load_dataset_builder`` failure modes
    (missing extra, malformed id, info-shape drift, offline). A broad
    ``Exception`` here would hide genuine bugs in the rest of the
    manifest pipeline.
    """
    try:
        from datasets import load_dataset_builder

        builder = load_dataset_builder(dataset_path)
        if builder.info.version:
            fingerprint["version"] = str(builder.info.version)
        if builder.info.description:
            fingerprint["description"] = builder.info.description[:200]
        if builder.info.download_size:
            fingerprint["download_size_bytes"] = builder.info.download_size
    except (
        ImportError,
        FileNotFoundError,
        ValueError,
        AttributeError,
        ConnectionError,
        TimeoutError,
    ) as e:
        logger.debug("HF Hub metadata fetch skipped for '%s': %s", dataset_path, e)


def _fingerprint_hf_revision(dataset_path: str, fingerprint: Dict[str, Any]) -> None:
    """Closure plan Faz 3 (F-compliance-117): pin the Hub commit SHA.

    The only stable identifier that lets Article 10 reviewers reproduce
    the exact corpus the model was trained on. ``HfApi.dataset_info`` is
    part of the always-installed ``huggingface_hub`` (pulled in by
    ``datasets``).

    Two-layer error handling so the failure mode is informative:

    1. Module import is guarded separately — if ``huggingface_hub`` is
       missing it's an environment issue, not a transient API hiccup.
    2. The actual ``dataset_info`` call uses a broad ``Exception`` catch
       (with ``# noqa: BLE001`` justification) because the HF Hub client
       surface raises a long tail of error types (``HfHubHTTPError``,
       ``RepositoryNotFoundError``, ``RevisionNotFoundError``, plus the
       transport ``OSError``/``ValueError`` family). Enumerating them
       couples ``compliance.py`` to ``huggingface_hub`` internals;
       failing best-effort is the documented contract.
    """
    try:
        from huggingface_hub import HfApi
    except ImportError as e:
        logger.debug("HF Hub revision pin skipped for '%s' — huggingface_hub not installed: %s", dataset_path, e)
        return

    try:
        info = HfApi().dataset_info(dataset_path)
        revision_sha = getattr(info, "sha", None)
        if revision_sha:
            fingerprint["hf_revision"] = revision_sha
    except Exception as e:  # noqa: BLE001 — best-effort revision pin; HF Hub surface raises a wide error tail
        logger.debug("HF Hub revision pin skipped for '%s': %s", dataset_path, e)


def compute_dataset_fingerprint(dataset_path: str) -> Dict[str, Any]:
    """Compute a fingerprint for a dataset file or directory.

    The previous version was decorated with ``@lru_cache(maxsize=32)`` keyed
    only on the path string. Three problems compounded:

    1. **TOCTOU**: a long-running process that audits the same path twice
       (training restart, multi-stage pipeline) would return the *first*
       fingerprint even after the file had been rewritten — silently
       producing stale Article 10 evidence.
    2. **No symlink resolution**: ``./data.jsonl`` and a symlink to it
       hashed independently; mutating the target invalidated only one
       cache entry.
    3. **Non-atomic stat + read**: ``os.stat()`` and the subsequent open
       read could race a concurrent writer.

    The cache is dropped (cost is dominated by the file read anyway, and
    a per-process memo would still suffer the staleness problem); symlinks
    are resolved before hashing; ``stat`` is captured from the same open
    file descriptor as the SHA-256 stream so the triple is consistent.

    Per-source helpers (``_fingerprint_local_file`` /
    ``_fingerprint_hf_metadata`` / ``_fingerprint_hf_revision``) keep the
    orchestrator linear; this function just routes by source kind.
    """
    fingerprint = {
        "path": dataset_path,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if os.path.isfile(dataset_path):
        _fingerprint_local_file(dataset_path, fingerprint)
    else:
        fingerprint["source"] = "huggingface_hub"
        fingerprint["dataset_id"] = dataset_path
        _fingerprint_hf_metadata(dataset_path, fingerprint)
        _fingerprint_hf_revision(dataset_path, fingerprint)

    return fingerprint


# ---------------------------------------------------------------------------
# Art. 11 + Annex IV: Training Manifest & Technical Documentation
# ---------------------------------------------------------------------------


def compute_config_hash(config: Any) -> str:
    """Canonical SHA-256 digest of the validated config that ran.

    Binds the single-run training manifest / approval row / JSON envelope
    to the configuration that produced the run, mirroring the multi-stage
    pipeline's ``pipeline_config_hash`` (``_compute_pipeline_config_hash``)
    so a verifier can recompute one digest across both paths.

    The pipeline hashes the *raw YAML bytes*; the single-run path only
    holds the validated :class:`ForgeConfig` object, so we serialise the
    redacted ``model_dump(mode="json")`` with ``sort_keys=True`` for a
    stable, order-independent canonical form.

    Partial secret redaction: ``AuthConfig.hf_token`` and
    ``SyntheticConfig.api_key`` are redacted by their per-block
    ``model_dump`` overrides.  ``WebhookConfig.url`` (which may carry an
    embedded Slack/Teams OAuth token in the URL path) has **no**
    ``model_dump`` override and is included verbatim in the canonical
    JSON.  The hash itself is a one-way digest and does not expose the
    URL on disk, but a verifier attempting to reproduce the digest from a
    stored config would need the unredacted URL.  Operators that treat
    the webhook URL as a secret should use ``url_env`` indirection
    instead of ``url`` directly (F-M-11).

    Returns ``"sha256:" + hexdigest`` to match the pipeline format.
    """
    try:
        payload = config.model_dump(mode="json")
    except AttributeError:
        # Hand-rolled config dicts / pre-pydantic-v2 callers: hash the repr
        # rather than crash. Better a coarse digest than no binding at all.
        payload = repr(config)
    canonical = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def generate_training_manifest(
    config: Any,
    metrics: Dict[str, float],
    resource_usage: Optional[Dict[str, Any]] = None,
    safety_result: Optional[Dict[str, Any]] = None,
    judge_result: Optional[Dict[str, Any]] = None,
    benchmark_result: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a comprehensive training manifest for audit purposes.

    ``run_id`` (when supplied) and ``config_hash`` bind the manifest to the
    specific run + the config that produced it, so a post-training config
    edit before export is detectable (F-P4-OPUS-13 / XP-11).
    """
    manifest = {
        "forgelm_version": _get_version(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config_hash": compute_config_hash(config),
        "model_lineage": {
            "base_model": config.model.name_or_path,
            "backend": config.model.backend,
            "adapter_method": _describe_adapter_method(config),
            "quantization": "4-bit NF4" if config.model.load_in_4bit else "none",
            "trust_remote_code": config.model.trust_remote_code,
        },
        "training_parameters": {
            "trainer_type": config.training.trainer_type,
            "epochs": config.training.num_train_epochs,
            "batch_size": config.training.per_device_train_batch_size,
            "gradient_accumulation_steps": config.training.gradient_accumulation_steps,
            "learning_rate": config.training.learning_rate,
            "max_length": config.model.max_length,
            "lora_r": config.lora.r,
            "lora_alpha": config.lora.alpha,
            "lora_dropout": config.lora.dropout,
            "dora": config.lora.use_dora,
            "target_modules": config.lora.target_modules,
        },
        "data_provenance": {
            "primary_dataset": config.data.dataset_name_or_path,
            "fingerprint": compute_dataset_fingerprint(config.data.dataset_name_or_path),
            "shuffle": config.data.shuffle,
            "clean_text": config.data.clean_text,
        },
        "evaluation_results": {
            "metrics": metrics,
        },
    }

    # Annex IV provider metadata
    comp_cfg = getattr(config, "compliance", None)
    if comp_cfg:
        manifest["annex_iv"] = {
            "provider_name": comp_cfg.provider_name,
            "provider_contact": comp_cfg.provider_contact,
            "system_name": comp_cfg.system_name,
            "intended_purpose": comp_cfg.intended_purpose,
            "known_limitations": comp_cfg.known_limitations,
            "system_version": comp_cfg.system_version,
            "risk_classification": comp_cfg.risk_classification,
        }

    # Risk assessment
    risk_cfg = getattr(config, "risk_assessment", None)
    if risk_cfg:
        manifest["risk_assessment"] = {
            "intended_use": risk_cfg.intended_use,
            "foreseeable_misuse": risk_cfg.foreseeable_misuse,
            "risk_category": risk_cfg.risk_category,
            "mitigation_measures": risk_cfg.mitigation_measures,
            "vulnerable_groups_considered": risk_cfg.vulnerable_groups_considered,
        }

    # Extra datasets provenance
    extra_datasets = getattr(config.data, "extra_datasets", None)
    if extra_datasets:
        manifest["data_provenance"]["extra_datasets"] = [
            {"path": p, "fingerprint": compute_dataset_fingerprint(p)} for p in extra_datasets
        ]

    # Monitoring config
    mon_cfg = getattr(config, "monitoring", None)
    if mon_cfg and mon_cfg.enabled:
        manifest["monitoring"] = {
            "endpoint": mon_cfg.endpoint or f"${mon_cfg.endpoint_env}",
            "metrics_export": mon_cfg.metrics_export,
            "alert_on_drift": mon_cfg.alert_on_drift,
            "check_interval_hours": mon_cfg.check_interval_hours,
        }

    # Webhook config — preserved into the compliance report so the
    # post-training approve / reject dispatchers (which run with no --config
    # flag, only the output_dir) can rebuild a WebhookNotifier from the
    # co-located JSON.  Without this the operator's Slack / Teams hook
    # configured in the original training YAML produces a silent no-op on
    # ``forgelm approve`` / ``forgelm reject`` because
    # ``_build_approval_notifier`` reads ``webhook_config`` from this exact
    # report and would otherwise see ``None``.  The persisted field set is the
    # module-level ``_WEBHOOK_PERSIST_FIELDS`` (derived from the live
    # ``WebhookConfig`` schema, ``url`` excluded — see its definition for the
    # secret-leak and schema-drift rationale).
    webhook_cfg = getattr(config, "webhook", None)
    if webhook_cfg is not None:
        try:
            dumped = webhook_cfg.model_dump(mode="json")
            manifest["webhook_config"] = {k: dumped.get(k) for k in _WEBHOOK_PERSIST_FIELDS}
        except AttributeError:
            # Defensive — pre-pydantic-v2 callers or hand-rolled config dicts.
            # Falls through to a best-effort attribute dump so the approve /
            # reject dispatchers still see *something* rather than a silent
            # absent key.  ``url`` is intentionally absent from the field
            # set so the credential never reaches disk via this branch.
            manifest["webhook_config"] = {k: getattr(webhook_cfg, k, None) for k in _WEBHOOK_PERSIST_FIELDS}

    if run_id:
        manifest["run_id"] = run_id

    if resource_usage:
        manifest["resource_usage"] = resource_usage
    if safety_result:
        manifest["evaluation_results"]["safety"] = safety_result
    if judge_result:
        manifest["evaluation_results"]["llm_judge"] = judge_result
    if benchmark_result:
        manifest["evaluation_results"]["benchmark"] = benchmark_result

    return manifest


# ---------------------------------------------------------------------------
# Art. 13: Deployer Instructions
# ---------------------------------------------------------------------------


# CommonMark special characters that must be backslash-escaped when embedding
# user-controlled text in inline Markdown contexts (table cells, headings, etc.).
# Source: https://spec.commonmark.org/0.31.2/#backslash-escapes
_COMMONMARK_SPECIALS = frozenset(r'!"#$%&\'()*+,-./:;<=>?@[\]^_`{|}~')


def _sanitize_md(text: Optional[str]) -> str:
    """Escape user-controlled text before embedding in Markdown to prevent injection.

    Escapes the full CommonMark special-character set so operator-supplied fields
    (``provider_name``, ``intended_purpose``, etc.) cannot create links, headers,
    code spans, or table breaks in the generated deployer instructions.

    Accepts ``None`` (treated as "Not specified") so callers can pass through
    optional config fields without a per-site None-check.
    """
    if not text:
        return "Not specified"
    # Collapse newlines first so they don't break table cell boundaries
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    # Backslash-escape every CommonMark special character
    escaped = "".join(("\\" + ch if ch in _COMMONMARK_SPECIALS else ch) for ch in text)
    return escaped.strip()


def _sanitize_md_list(items: Optional[List[Any]]) -> List[str]:
    """Apply :func:`_sanitize_md` element-wise to ``items``.

    Wave 3 / Faz 28 (M-204): a small ergonomic shim used by the
    deployer-instructions builder when interpolating list-shaped
    config fields (foreseeable misuse list, dataset names, etc.) into
    Markdown bullets / table rows.  Centralises the per-element
    sanitisation so a future migration to a stricter escape policy
    only has to touch :func:`_sanitize_md`.

    Returns ``[]`` for ``None`` / empty inputs so callers can spread
    the result directly into a join without a None guard.  Non-string
    elements are stringified first (mirrors :func:`_sanitize_md`'s
    permissive ``Any`` shape — operators occasionally drop ints into
    list fields).
    """
    if not items:
        return []
    return [_sanitize_md(str(item) if not isinstance(item, str) else item) for item in items]


def generate_deployer_instructions(config: Any, metrics: Dict[str, float], final_path: str) -> str:
    """Generate deployer instructions document per EU AI Act Article 13."""
    comp_cfg = getattr(config, "compliance", None)
    risk_cfg = getattr(config, "risk_assessment", None)

    provider = _sanitize_md(comp_cfg.provider_name if comp_cfg else "")
    purpose = _sanitize_md(comp_cfg.intended_purpose if comp_cfg else "")
    limitations = _sanitize_md(comp_cfg.known_limitations if comp_cfg else "")
    # Every field below is interpolated into Markdown table cells, headings,
    # or bullet bodies — push each through ``_sanitize_md`` so config-derived
    # strings cannot inject pipes, headings, code spans, or links into the
    # generated document.
    raw_system_name = comp_cfg.system_name if comp_cfg else config.model.name_or_path.split("/")[-1]
    system_name = _sanitize_md(raw_system_name)
    base_model = _sanitize_md(config.model.name_or_path)
    fine_tuning_method = _sanitize_md(_describe_adapter_method(config))
    model_location = _sanitize_md(final_path)

    content = f"""# Deployer Instructions — {system_name}

> Auto-generated by ForgeLM v{_get_version()} per EU AI Act Article 13.
> This document is intended for personnel deploying this model in production.

## 1. System Identity

| Field | Value |
|-------|-------|
| System Name | {system_name} |
| Provider | {provider} |
| Base Model | {base_model} |
| Fine-Tuning Method | {fine_tuning_method} |
| Model Location | {model_location} |

## 2. Intended Purpose

{purpose}

## 3. Known Limitations

{limitations}

**This model should NOT be used for:**
"""
    if risk_cfg and risk_cfg.foreseeable_misuse:
        for misuse in _sanitize_md_list(risk_cfg.foreseeable_misuse):
            content += f"- {misuse}\n"
    else:
        content += "- Use cases not covered by the intended purpose above\n"

    content += """
## 4. Performance Metrics

| Metric | Value |
|--------|-------|
"""
    for k, v in sorted(metrics.items()):
        if isinstance(v, float):
            content += f"| {_sanitize_md(k)} | {v:.4f} |\n"

    content += """
## 5. Human Oversight Requirements

- A qualified human operator must review model outputs before they are used in consequential decisions.
- The operator must be able to override or discard model outputs.
- Incident reporting: contact the provider if the model produces harmful, incorrect, or unexpected outputs.

## 6. Hardware Requirements

- The model requires a GPU with sufficient VRAM for inference.
- Minimum: NVIDIA GPU with 8GB+ VRAM (for quantized inference).
- Recommended: NVIDIA A100/H100 for production workloads.

## 7. Incident Reporting

If the model produces harmful, biased, or incorrect outputs in production:
1. Document the input that caused the issue
2. Stop using the model for that use case
3. Report to the provider immediately
"""

    doc_path = os.path.join(final_path, "deployer_instructions.md")
    os.makedirs(final_path, exist_ok=True)
    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info("Deployer instructions saved to %s", doc_path)
    return doc_path


# ---------------------------------------------------------------------------
# Annex IV §1-9 canonical layout (writer + hash for verify-annex-iv)
# ---------------------------------------------------------------------------


def build_annex_iv_artifact(manifest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Synthesise the EU AI Act Annex IV §1-9 canonical artifact from the
    training manifest produced by :func:`generate_training_manifest`.

    The verifier (``forgelm verify-annex-iv``) checks nine top-level
    categories (per Annex IV §1-9); the training manifest carries
    closely-related but differently-shaped sub-blocks
    (``model_lineage``, ``training_parameters``, ``data_provenance``,
    ``annex_iv``, ``risk_assessment``, etc.).  This helper bridges the
    two so a freshly-generated artefact passes its own verifier.

    Returns ``None`` when the manifest lacks the operator-supplied
    Annex IV metadata block (``manifest["annex_iv"]``) — without that,
    the §1 system identification cannot be populated and the verifier
    would reject the artefact as incomplete anyway.  Skipping the file
    is more honest than emitting a half-populated stub.

    The returned dict carries a ``metadata.manifest_hash`` stamp via
    :func:`compute_annex_iv_manifest_hash` so the verifier's tampering-
    detection branch fires.

    Wave 2b Round-4 review F-W2B-01 + F-W2B-05:  previously the writer
    emitted the operator-supplied 7-key provider block verbatim, which
    is operator-friendly but does not match the §1-9 verifier surface.
    The new layout keeps the original block intact via
    ``provider_metadata`` so existing tooling that reads it does not
    break, and surfaces the §1-9 keys at the top level for verifier
    compatibility.
    """
    operator_block = manifest.get("annex_iv")
    if not isinstance(operator_block, dict):
        return None

    # §1 identity-critical sub-fields: skip the file (as we do for an
    # absent block) when provider_name / system_name / intended_purpose
    # are all blank, rather than emit a §1 stub the verifier must then
    # catch (F-P4-OPUS-17).  Mirrors the verifier's nested-completeness
    # check so the writer never produces an artefact that fails its own
    # verifier on the §1 gate.
    if not any(
        str(operator_block.get(subkey, "")).strip() for subkey in ("provider_name", "system_name", "intended_purpose")
    ):
        return None

    artifact: Dict[str, Any] = {
        # Annex IV §1: system identification + intended purpose.  Pulled
        # from the operator-supplied compliance block; the verifier
        # accepts a dict shape.
        "system_identification": {
            "provider_name": operator_block.get("provider_name", ""),
            "provider_contact": operator_block.get("provider_contact", ""),
            "system_name": operator_block.get("system_name", ""),
            "system_version": operator_block.get("system_version", ""),
            "intended_purpose": operator_block.get("intended_purpose", ""),
            "risk_classification": operator_block.get("risk_classification", "minimal-risk"),
        },
        # Top-level duplicate of the §1 intended-purpose: the Annex IV verifier
        # (``cli/subcommands/_verify_annex_iv.py``) lists ``intended_purpose`` as
        # a required top-level §1 field in ``_ANNEX_IV_REQUIRED_FIELDS`` and fails
        # the artefact if it is absent, so this is a load-bearing consumer, not
        # leftover duplication. Keep it in lockstep with ``system_identification``.
        "intended_purpose": operator_block.get("intended_purpose", ""),
        # Annex IV §2: software / hardware components + supplier list.
        # Synthesised from the manifest's model lineage + training
        # hyperparameters so the auditor can reconstruct what was run.
        "system_components": {
            "model_lineage": manifest.get("model_lineage", {}),
            "training_parameters": manifest.get("training_parameters", {}),
        },
        "computational_resources": (
            manifest.get("resource_usage")
            or manifest.get("training_parameters", {}).get("resource_usage")
            or {"recorded": "see resource_usage block when training runs with --resource-tracking"}
        ),
        # Annex IV §2(d): data sources, governance, validation methodology.
        "data_governance": manifest.get("data_provenance", {}),
        # Annex IV §3-5: design + development methodology.
        "technical_documentation": {
            "forgelm_version": manifest.get("forgelm_version", ""),
            "generated_at": manifest.get("generated_at", ""),
            "known_limitations": operator_block.get("known_limitations", ""),
        },
        # Annex IV §6: post-market monitoring + audit-log presence.
        "monitoring_and_logging": (manifest.get("monitoring") or {"audit_log": "audit_log.jsonl"}),
        # Annex IV §7: accuracy / robustness metrics.
        "performance_metrics": manifest.get("evaluation_results", {}).get("metrics", {}),
        # Annex IV §9: risk management system reference.
        "risk_management": manifest.get("risk_assessment")
        or {
            "art9_reference": "no risk_assessment block configured",
        },
        # Operator-friendly view: keep the original 7-key provider block
        # under a separate top-level key so existing downstream tooling
        # that reads `compliance_block` directly does not break.
        "provider_metadata": dict(operator_block),
    }

    # Stamp manifest_hash so the verifier's tampering-detection branch
    # fires.  Computed AFTER the §1-9 fields are populated so the hash
    # covers the full payload.  ``metadata`` carries the hash of everything
    # else, so it is added after the hash is computed (chicken-and-egg).
    # Insertion order is irrelevant: ``compute_annex_iv_manifest_hash``
    # strips the metadata block and serialises with ``sort_keys=True``, so
    # neither this block's presence nor its position can affect the digest.
    artifact["metadata"] = {"manifest_hash": compute_annex_iv_manifest_hash(artifact)}
    return artifact


def _manifest_json_default(o: Any) -> Any:
    """``json.dumps`` fallback that serialises sets deterministically.

    A bare ``default=str`` stringifies a ``set``/``frozenset`` (e.g. a
    de-duplicated LoRA ``target_modules``) in PYTHONHASHSEED-dependent
    iteration order, so the same artefact hashes differently across two
    processes — a false-tampering verdict.  Emitting ``sorted(list(o))``
    pins the on-disk shape so the verifier re-hashes a deterministic
    structure (F-P4-OPUS-16).
    """
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    return str(o)


def compute_annex_iv_manifest_hash(artifact: Dict[str, Any]) -> str:
    """Canonical SHA-256 over the artifact MINUS its metadata block.

    Both the writer (:func:`build_annex_iv_artifact`) and the verifier
    (``forgelm verify-annex-iv``) call this helper so the
    canonicalisation cannot drift byte-for-byte across the two paths.

    Strips ``metadata.manifest_hash`` and ``metadata.manifest_signature``
    before serialisation (those are derived from the rest of the
    artefact and would otherwise create a chicken-and-egg cycle).
    Serialises the rest with ``sort_keys=True, separators=(",", ":")``
    so non-significant whitespace + key ordering does not affect the
    digest.

    The payload is normalised through ``json.loads(json.dumps(...,
    default=_manifest_json_default))`` *before* the canonical dump so the
    writer (which hashes the in-memory dict) and the verifier (which
    hashes the dict read back from disk) operate on byte-identical
    structures even when the artefact carries non-JSON-native content.
    Without this, an integrator passing a manifest with integer dict keys
    or a ``set`` (e.g. de-duplicated ``target_modules``) would get a
    false-tampering verdict: ``sort_keys=True`` orders integer keys
    numerically pre-disk but lexicographically once they round-trip to
    strings, and a bare ``default=str`` would stringify a set in
    PYTHONHASHSEED-dependent order.  ``_manifest_json_default`` emits a
    sorted list for sets so the digest is deterministic across processes
    (F-P4-OPUS-16).  The config-driven path only ever feeds JSON-native
    types, so this is a no-op there; it closes the gap for the
    documented public library entry ``build_annex_iv_artifact``.
    """
    import hashlib as _hashlib

    # Normalise to the post-default shape the verifier will see on disk
    # (this also deep-copies, so the metadata strip below does not mutate
    # the caller's dict).  Sets/frozensets serialise to a sorted list so
    # the digest is deterministic across PYTHONHASHSEED — ``str(set)``
    # emits members in hash-randomised order, producing a different hash
    # in a second process and a false-tampering verdict (F-P4-OPUS-16).
    payload = json.loads(json.dumps(artifact, default=_manifest_json_default))
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("manifest_hash", None)
        metadata.pop("manifest_signature", None)
        # Drop the now-empty metadata block so an artefact written
        # without metadata at all hashes identically to one whose
        # metadata block carried only the (now-stripped) hash.
        if not metadata:
            payload.pop("metadata", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return _hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Phase 14 — Pipeline-level Annex IV manifest
# ---------------------------------------------------------------------------
#
# The canonical ``generate_pipeline_manifest`` + ``export_pipeline_manifest``
# definitions live further down in this module, alongside the
# ``_provider_metadata_from_config`` helper and ``verify_pipeline_manifest``
# disk-bound verifier.  Only the structural / chain-integrity helper
# (``_verify_manifest_payload``) is declared here; the rest of the
# pipeline-manifest surface clusters near the verifier so reviewers can
# eyeball the schema and its validator together.


_PIPELINE_MANIFEST_REQUIRED_KEYS = (
    "forgelm_version",
    "pipeline_run_id",
    "pipeline_config_hash",
    "started_at",
    "final_status",
    "stages",
)


def _check_chain_link(idx: int, stage: Dict[str, Any], stages: List[Dict[str, Any]]) -> Optional[str]:
    """Single-stage chain-integrity check.

    Returns a violation string when stage *idx* (claiming
    ``input_source == "chain"``) cannot be validated against its
    immediate predecessor's ``output_model``; ``None`` when the link
    is intact (or when the stage's ``input_source`` is not ``chain``
    in which case the caller should never have invoked this).
    Extracted from :func:`_verify_manifest_payload` for Sonar
    python:S3776 cognitive-complexity hygiene.
    """
    if idx == 0:
        return f"Stage {stage.get('name')!r}: input_source='chain' but no previous stage exists (stage 0 cannot chain)."
    prev = stages[idx - 1]
    prev_output_model = prev.get("output_model")
    if not prev_output_model:
        return (
            f"chain_integrity_violation at stage {stage.get('name')!r}: claims "
            f"input_source='chain' but previous stage {prev.get('name')!r} has "
            f"no output_model (status={prev.get('status')!r})."
        )
    if stage.get("input_model") != prev_output_model:
        return (
            f"chain_integrity_violation at stage {stage.get('name')!r}: input_model={stage.get('input_model')!r} "
            f"≠ previous output_model={prev_output_model!r}"
        )
    return None


def _check_status_consistency(manifest: Dict[str, Any], stages: List[Dict[str, Any]]) -> List[str]:
    """Status-consistency checks for a finalised pipeline manifest.

    Covers (a) ``stopped_at`` referent existence + expected status and
    (b) the no-``running``-stages-on-a-finalised-manifest rule (F-N-2).
    Extracted from :func:`_verify_manifest_payload` for Sonar
    python:S3776 cognitive-complexity hygiene.
    """
    violations: List[str] = []
    stopped_at = manifest.get("stopped_at")
    if stopped_at is not None:
        matching = [s for s in stages if s.get("name") == stopped_at]
        if not matching:
            violations.append(f"stopped_at refers to unknown stage {stopped_at!r}")
        elif matching[0].get("status") not in ("failed", "gated_pending_approval"):
            violations.append(
                f"stopped_at stage {stopped_at!r} has status {matching[0].get('status')!r} "
                f"(expected `failed` or `gated_pending_approval`)"
            )

    # Running-stage consistency (N-2): a finalised manifest with a stage
    # still in ``running`` indicates the orchestrator crashed mid-stage.
    final_status = manifest.get("final_status")
    if final_status and final_status != "in_progress":
        running_stages = [s.get("name") for s in stages if s.get("status") == "running"]
        if running_stages:
            violations.append(
                f"stage(s) {running_stages!r} still in `running` status on a "
                f"finalised manifest (final_status={final_status!r}); the "
                f"orchestrator likely crashed mid-stage without updating state."
            )
    return violations


def _verify_manifest_payload(manifest: Dict[str, Any]) -> List[str]:
    """Validate a pipeline manifest's structural + chain-integrity rules.

    Returns a list of human-readable violation strings (empty list ⇒
    manifest is valid).  Used by ``forgelm verify-annex-iv --pipeline
    <run_dir>`` to surface integrity issues to operators / regulators.

    Checks:

    1. **Required top-level keys** are present and of the right shape.
    2. **Chain integrity** — for every stage N with
       ``input_source == "chain"``, the *immediate* previous stage's
       ``output_model`` must match its ``input_model``.  If the previous
       stage has no ``output_model`` (e.g. failed before saving) the
       chain link is unreconstructible and the verifier flags it as a
       ``chain_integrity_violation`` (Phase 14 review F-B-3 hardening:
       pre-fix the verifier walked across stages that *had* an
       output_model, silently accepting a manifest whose chain could
       not actually be reconstructed).  Stages with
       ``input_source != "chain"`` (``root`` / ``stage_explicit`` /
       ``cli_override``) intentionally break the chain — by design,
       reviewers inspect the audit log to validate them.
    3. **Status consistency** — at most one ``stopped_at`` stage; if
       set, that stage's status must be one of ``failed`` /
       ``gated_pending_approval``.  Additionally, a finalised manifest
       (``final_status != "in_progress"``) must not carry any stage
       still in ``running`` status — that indicates a process crash
       mid-stage that the archive must surface (Phase 14 review F-N-2).
    4. **Index monotonicity** — stage indices form 0..N-1 in order.
    5. **Content hash** — when ``metadata.manifest_hash`` is present, it
       is recomputed over the canonicalised manifest (minus the metadata
       block) and a mismatch is flagged.  Absence downgrades to
       structural-only verification (mirrors the single-stage Annex IV
       artefact's policy), so older manifests written before the hash
       was stamped still verify on their structural rules.
    """
    violations: List[str] = []

    for key in _PIPELINE_MANIFEST_REQUIRED_KEYS:
        if key not in manifest:
            violations.append(f"missing required top-level key: {key!r}")

    # Content-tamper detection (F-P4-OPUS-20).  When the manifest carries
    # a manifest_hash, recompute it; a mismatch means a stage field
    # (metrics, gate_decision, output_model, provider metadata) was
    # edited after generation in a way the structural/chain checks below
    # cannot see.
    metadata = manifest.get("metadata") if isinstance(manifest.get("metadata"), dict) else None
    expected_hash = metadata.get("manifest_hash") if metadata else None
    if expected_hash:
        actual_hash = compute_annex_iv_manifest_hash(manifest)
        if actual_hash != expected_hash:
            violations.append(
                "manifest hash mismatch — pipeline manifest may have been modified after "
                f"generation (expected {expected_hash[:16]}…, recomputed {actual_hash[:16]}…)."
            )

    stages = manifest.get("stages", [])
    if not isinstance(stages, list):
        violations.append("`stages` must be a list")
        return violations

    # Phase 14 review-response: per-item type-guard.  A tampered
    # manifest (``stages: [null, "foo"]``) would otherwise raise
    # ``AttributeError`` on ``s.get(...)`` partway through the
    # verifier.  Surface as a structured violation so the disk wrapper
    # (and any future caller) gets a clean list back.  We filter the
    # malformed items out of the rest of the checks because the
    # downstream helpers also expect dicts.
    well_formed_stages: List[Dict[str, Any]] = []
    for idx, s in enumerate(stages):
        if not isinstance(s, dict):
            violations.append(f"stage at index {idx} is not an object (got {type(s).__name__})")
            continue
        well_formed_stages.append(s)

    # Index monotonicity (on the well-formed subset; mis-indexing of
    # a malformed entry would be a noise violation on top of the
    # already-reported type error).
    for expected, s in enumerate(well_formed_stages):
        if s.get("index") != expected:
            violations.append(f"stage index out of order at position {expected}: got {s.get('index')!r}")

    # Chain integrity (one check per chain-stage; helper is pure).
    for idx, s in enumerate(well_formed_stages):
        if s.get("input_source") != "chain":
            continue
        link_violation = _check_chain_link(idx, s, well_formed_stages)
        if link_violation is not None:
            violations.append(link_violation)

    # Status consistency (stopped_at + running-on-finalised).
    violations.extend(_check_status_consistency(manifest, well_formed_stages))

    return violations


# ---------------------------------------------------------------------------
# Export: All Compliance Artifacts
# ---------------------------------------------------------------------------


def export_compliance_artifacts(
    manifest: Dict[str, Any],
    output_dir: str,
) -> List[str]:
    """Export all compliance artifacts to a directory.

    The *manifest* (produced by :func:`generate_training_manifest`) already
    contains all the config-derived data needed for the artifacts, so the
    config object itself is not required here.

    The five Annex IV artefacts are written all-or-nothing (F-P4-OPUS-10 /
    XP-12): each file is first written into a sibling ``.export-tmp`` staging
    directory, and only after every write succeeds are they promoted into
    *output_dir* with :func:`os.replace`.  Promotion itself is also
    all-or-nothing: each pre-existing target is backed up into the staging
    dir before it is overwritten, and a mid-promotion failure (disk full,
    SIGKILL between renames) rolls the published bundle back to its previous
    state before re-raising — a reader never observes a torn bundle that
    mixes new and old artefacts.  Mirrors the tmp+rename discipline of
    :func:`export_pipeline_manifest`.
    """
    import shutil
    import tempfile

    os.makedirs(output_dir, exist_ok=True)

    # Staging dir as a sibling of output_dir so the os.replace promotion is a
    # same-filesystem atomic rename per file.
    staging_dir = tempfile.mkdtemp(prefix=".export-tmp-", dir=output_dir)
    # (staging filename, final filename) pairs, in promotion order.
    pending: List[Tuple[str, str]] = []

    try:
        import yaml

        # 1. Full compliance report (JSON)
        with open(os.path.join(staging_dir, "compliance_report.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, default=str)
        pending.append(("compliance_report.json", "compliance_report.json"))

        # 2. Training manifest (YAML)
        yaml_manifest = {
            "forgelm_version": manifest["forgelm_version"],
            "generated_at": manifest["generated_at"],
            "base_model": manifest["model_lineage"]["base_model"],
            "adapter_method": manifest["model_lineage"]["adapter_method"],
            "trainer_type": manifest["training_parameters"]["trainer_type"],
            "dataset": manifest["data_provenance"]["primary_dataset"],
            "epochs": manifest["training_parameters"]["epochs"],
            "final_metrics": {
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in manifest["evaluation_results"]["metrics"].items()
                if not k.startswith("benchmark/")
            },
        }
        with open(os.path.join(staging_dir, "training_manifest.yaml"), "w", encoding="utf-8") as f:
            yaml.dump(yaml_manifest, f, default_flow_style=False, sort_keys=False)
        pending.append(("training_manifest.yaml", "training_manifest.yaml"))

        # 3. Data provenance (JSON)
        with open(os.path.join(staging_dir, "data_provenance.json"), "w", encoding="utf-8") as f:
            json.dump(manifest["data_provenance"], f, indent=2, default=str)
        pending.append(("data_provenance.json", "data_provenance.json"))

        # 4. Risk assessment (JSON) — if present
        if "risk_assessment" in manifest:
            with open(os.path.join(staging_dir, "risk_assessment.json"), "w", encoding="utf-8") as f:
                json.dump(manifest["risk_assessment"], f, indent=2)
            pending.append(("risk_assessment.json", "risk_assessment.json"))

        # 5. Annex IV metadata (JSON) — emitted in the §1-9 canonical layout
        # the verifier expects, with a manifest_hash stamp so tampering is
        # detectable.  Wave 2b Round-4 review F-W2B-01 + F-W2B-05 fix:
        # previously this wrote the flat 7-key provider-metadata block
        # (provider_name / system_name / etc.) which the verifier rejected
        # as missing 8 of 9 required fields, AND never emitted a
        # manifest_hash so the verifier silently skipped tampering
        # detection.  build_annex_iv_artifact synthesises the §1-9 keys
        # from the manifest sub-blocks; compute_annex_iv_manifest_hash
        # produces a hash the verifier recomputes byte-for-byte.
        annex_artifact = build_annex_iv_artifact(manifest)
        if annex_artifact is not None:
            with open(os.path.join(staging_dir, "annex_iv_metadata.json"), "w", encoding="utf-8") as f:
                # Must use _manifest_json_default (not default=str) so sets/frozensets
                # are serialised as sorted lists — matching what compute_annex_iv_manifest_hash
                # normalises to when computing the stored digest.  default=str would
                # emit a PYTHONHASHSEED-dependent string like "{'q_proj', 'v_proj'}" while
                # the verifier re-hashes a list, producing a false-tampering verdict
                # (F-H-05).
                json.dump(annex_artifact, f, indent=2, default=_manifest_json_default)
            pending.append(("annex_iv_metadata.json", "annex_iv_metadata.json"))

        # All writes succeeded — promote into place.  os.replace is atomic
        # per file, but a multi-file bundle is not atomic across files: a
        # mid-loop failure (disk full on file 3) would leave files 1-2
        # published while the rest are dropped on staging cleanup — a torn
        # bundle a reader cannot distinguish from a complete one
        # (F-P4-OPUS-10).  Make promotion all-or-nothing by backing up any
        # pre-existing target into the staging dir before overwriting it; on
        # any failure, restore every backup and remove the files promoted so
        # far, leaving the OLD bundle (or an empty dir) intact.  The caller
        # records the failure via the ``compliance.artifacts_export_failed``
        # audit event when this function re-raises.
        generated_files: List[str] = []
        promoted: List[str] = []  # final paths newly written this run
        backups: List[Tuple[str, str]] = []  # (final_path, backup_path) pairs
        try:
            for staging_name, final_name in pending:
                final_path = os.path.join(output_dir, final_name)
                if os.path.exists(final_path):
                    backup_path = os.path.join(staging_dir, final_name + ".prev")
                    os.replace(final_path, backup_path)
                    backups.append((final_path, backup_path))
                os.replace(os.path.join(staging_dir, staging_name), final_path)
                promoted.append(final_path)
                generated_files.append(final_path)
        except OSError:
            # Roll back: drop the files we just promoted, then restore the
            # backed-up originals so the published bundle is exactly what it
            # was before this run.
            rollback_errors: List[str] = []
            for final_path in promoted:
                try:
                    os.remove(final_path)
                except OSError as exc:
                    rollback_errors.append(f"remove failed for {final_path!r}: {exc}")
            for final_path, backup_path in backups:
                try:
                    os.replace(backup_path, final_path)
                except OSError as exc:
                    rollback_errors.append(f"restore failed for {final_path!r} from {backup_path!r}: {exc}")
            if rollback_errors:
                logger.error(
                    "Compliance rollback encountered errors after failed promotion: %s",
                    "; ".join(rollback_errors),
                )
            raise
    finally:
        # Remove the staging dir whether we succeeded (now empty) or raised
        # (still holding un-promoted partial files) — no torn bundle is ever
        # left at output_dir, and no .export-tmp clutter survives.
        shutil.rmtree(staging_dir, ignore_errors=True)

    logger.info("Compliance report saved to %s", os.path.join(output_dir, "compliance_report.json"))
    logger.info("Compliance artifacts exported to %s (%d files)", output_dir, len(generated_files))
    return generated_files


# ---------------------------------------------------------------------------
# Evidence Bundle (ZIP)
# ---------------------------------------------------------------------------


def export_evidence_bundle(output_dir: str, bundle_path: str) -> str:
    """Package all compliance artifacts into a single auditor-ready ZIP archive.

    Written tmp+rename (F-P4-OPUS-33 / XP-12): the ZIP is built at
    ``<bundle_path>.tmp`` and promoted with :func:`os.replace` only after it
    is fully written and closed.  An interrupted run (SIGKILL, I/O error
    mid-walk) therefore never leaves a truncated/torn archive at the
    auditor-facing path and never clobbers a previously-valid bundle.
    """
    if not os.path.isdir(output_dir):
        logger.warning("Compliance directory not found: %s", output_dir)
        return ""

    tmp_path = bundle_path + ".tmp"
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(output_dir):
                for filename in files:
                    filepath = os.path.join(root, filename)
                    arcname = os.path.relpath(filepath, os.path.dirname(output_dir))
                    zf.write(filepath, arcname)
        os.replace(tmp_path, bundle_path)
    except BaseException:
        # On any failure (including SIGKILL-driven exceptions surfaced as
        # OSError mid-write) remove the partial tmp so it never lingers at a
        # path a reader might pick up; re-raise to surface the failure.
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    logger.info("Evidence bundle saved to %s", bundle_path)
    return bundle_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _describe_adapter_method(config: Any) -> str:
    parts = []
    method = getattr(config.lora, "method", "lora")
    if config.model.load_in_4bit:
        parts.append("QLoRA (4-bit NF4)")
    elif method == "pissa":
        parts.append("PiSSA")
    elif method == "rslora":
        parts.append("rsLoRA")
    else:
        parts.append("LoRA")
    if config.lora.use_dora or method == "dora":
        parts.append("DoRA")
    if getattr(config.training, "galore_enabled", False):
        parts.append(f"GaLore ({config.training.galore_optim})")
    parts.append(f"r={config.lora.r}")
    return " + ".join(parts)


def _get_version() -> str:
    """Return the runtime forgelm version for compliance / audit-log stamping."""
    return _forgelm_version


# ---------------------------------------------------------------------------
# Audit log verification (forgelm verify-audit)
# ---------------------------------------------------------------------------


@dataclass
class VerifyResult:
    """Outcome of :func:`verify_audit_log`.

    Attributes:
        valid: ``True`` when the SHA-256 hash chain (and optional HMAC tags)
            are intact across every entry. ``False`` on the first detected
            mismatch.
        entries_count: Number of newline-terminated JSON entries inspected.
        first_invalid_index: 1-based line number of the first invalid entry,
            or ``None`` when ``valid`` is ``True``.
        reason: Human-readable explanation of the first failure (chain
            break, HMAC mismatch, JSON decode error, manifest mismatch,
            missing-but-required HMAC tag), or ``None`` when valid.
    """

    valid: bool
    entries_count: int
    first_invalid_index: Optional[int] = None
    reason: Optional[str] = None


def _verify_hmac_for_entry(
    idx: int,
    entry: Dict[str, Any],
    hmac_secret: Optional[str],
    require_hmac: bool,
    entries_count: int,
) -> Optional[VerifyResult]:
    """Return None if HMAC check passes (or is skipped); a failing VerifyResult otherwise."""
    if hmac_secret is None and not require_hmac:
        return None
    tag = entry.get("_hmac")
    if tag is None:
        if require_hmac:
            return VerifyResult(
                valid=False,
                entries_count=entries_count,
                first_invalid_index=idx,
                reason=f"line {idx} lacks _hmac field but --require-hmac is set",
            )
        # Secret given but the writer wasn't keyed for this entry:
        # skip silently (mixed-mode logs are not a chain failure).
        return None
    if hmac_secret is None:
        return None
    run_id = entry.get("run_id")
    if not run_id:
        return VerifyResult(
            valid=False,
            entries_count=entries_count,
            first_invalid_index=idx,
            reason=f"line {idx} has _hmac but no run_id — cannot derive key",
        )
    # Mirror AuditLogger's key derivation byte-for-byte.
    key = hashlib.sha256(hmac_secret.encode() + run_id.encode()).digest()
    # Recompute the HMAC over the entry sans the _hmac field. Insertion
    # order is preserved by ``dict`` and ``log_event`` adds ``_hmac``
    # last, so removing it leaves the original ordering intact.
    entry_without_hmac = {k: v for k, v in entry.items() if k != "_hmac"}
    expected_tag = _hmac_module.new(
        key,
        json.dumps(entry_without_hmac, default=str).encode(),
        hashlib.sha256,
    ).hexdigest()
    if not _hmac_module.compare_digest(expected_tag, tag):
        return VerifyResult(
            valid=False,
            entries_count=entries_count,
            first_invalid_index=idx,
            reason=f"line {idx}: HMAC mismatch",
        )
    return None


def _verify_genesis_manifest(
    path: str,
    first_run_id: Optional[str],
    first_line_hash: Optional[str],
    entries_count: int,
) -> Optional[VerifyResult]:
    """Cross-check the ``<path>.manifest.json`` genesis pin; None on success."""
    manifest_path = path + ".manifest.json"
    if not os.path.isfile(manifest_path):
        logger.debug(
            "No genesis manifest at %s — truncate-and-resume detection limited to in-chain hash continuity.",
            manifest_path,
        )
        return None
    # Manifest is present (the truncate-and-resume detector). A
    # present-but-unreadable / present-but-malformed manifest is itself a
    # failure signal: an attacker who corrupted the manifest could be
    # disguising a chain rewrite. Fail verification rather than warning
    # and continuing.
    try:
        with open(manifest_path, "r", encoding="utf-8") as mfh:
            manifest = json.load(mfh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Audit genesis manifest unreadable (%s): %s", manifest_path, exc)
        return VerifyResult(
            valid=False,
            entries_count=entries_count,
            first_invalid_index=1,
            reason=f"manifest present but unreadable at {manifest_path!r}: {exc}",
        )
    pinned = manifest.get("first_entry_sha256")
    pinned_run = manifest.get("run_id")
    if not pinned or not pinned_run:
        return VerifyResult(
            valid=False,
            entries_count=entries_count,
            first_invalid_index=1,
            reason=(f"manifest missing required pinned fields (first_entry_sha256={pinned!r}, run_id={pinned_run!r})"),
        )
    # Truncate-to-empty: the manifest pins a real non-empty first entry, but the
    # log has zero entries. This is the exact truncation attack the manifest
    # exists to detect — the in-chain hash-continuity walk cannot catch it
    # because there are no lines left to walk. Fail loudly.
    if entries_count == 0:
        return VerifyResult(
            valid=False,
            entries_count=0,
            first_invalid_index=1,
            reason=(
                f"genesis manifest pins a first entry (first_entry_sha256={pinned!r}, "
                f"run_id={pinned_run!r}) but the audit log is empty — log truncated to zero entries"
            ),
        )
    if first_line_hash and pinned != first_line_hash:
        return VerifyResult(
            valid=False,
            entries_count=entries_count,
            first_invalid_index=1,
            reason=(
                "manifest mismatch: pinned first_entry_sha256 "
                f"{pinned!r} does not match line 1 hash {first_line_hash!r} "
                "(log may have been truncated and rewritten)"
            ),
        )
    if first_run_id and pinned_run != first_run_id:
        return VerifyResult(
            valid=False,
            entries_count=entries_count,
            first_invalid_index=1,
            reason=(f"manifest mismatch: pinned run_id {pinned_run!r} does not match line 1 run_id {first_run_id!r}"),
        )
    return None


def _verify_chain_walk(
    lines: List[str],
    hmac_secret: Optional[str],
    require_hmac: bool,
) -> VerifyResult:
    """Walk every line, verify chain + HMAC; return final VerifyResult.

    Returns valid=True with first_run_id and first_line_hash buried in the
    reason (not pretty, but keeps the public dataclass shape unchanged) —
    actually the caller passes those forward via the ``_chain_walk_state``
    closure. Simpler: we expose a private 2-tuple via ``reason`` only when
    valid; on failure ``reason`` is the human message.

    The orchestrator captures first_run_id/first_line_hash separately by
    re-parsing line 1 — cheaper than threading state through this helper.
    """
    entries_count = len(lines)
    expected_prev = "genesis"

    for idx, line in enumerate(lines, start=1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            return VerifyResult(
                valid=False,
                entries_count=entries_count,
                first_invalid_index=idx,
                reason=f"line {idx} is not valid JSON: {exc}",
            )

        if not isinstance(entry, dict):
            return VerifyResult(
                valid=False,
                entries_count=entries_count,
                first_invalid_index=idx,
                reason=f"line {idx} is not a JSON object",
            )

        prev_hash = entry.get("prev_hash")
        if prev_hash != expected_prev:
            return VerifyResult(
                valid=False,
                entries_count=entries_count,
                first_invalid_index=idx,
                reason=(f"chain broken at line {idx}: prev_hash={prev_hash!r} expected={expected_prev!r}"),
            )

        hmac_failure = _verify_hmac_for_entry(idx, entry, hmac_secret, require_hmac, entries_count)
        if hmac_failure is not None:
            return hmac_failure

        # Advance the chain. ``line`` here is the exact JSON body the
        # writer hashed (post-HMAC, without the trailing newline).
        expected_prev = hashlib.sha256(line.encode("utf-8")).hexdigest()

    return VerifyResult(valid=True, entries_count=entries_count)


def _read_audit_log_lines(path: str) -> Tuple[Optional[VerifyResult], List[str]]:
    """Stream the audit log line-by-line; return (failure-or-None, non-empty-lines).

    Streaming via line iteration avoids ``fh.read()`` into a single string
    which would balloon RAM for large logs. Lines are stripped of trailing
    newline so ``hashlib.sha256(line.encode("utf-8"))`` matches the writer's
    canonicalisation byte-for-byte.
    """
    if not os.path.isfile(path):
        return (
            VerifyResult(
                valid=False,
                entries_count=0,
                first_invalid_index=None,
                reason=f"audit log not found at {path!r}",
            ),
            [],
        )
    lines: List[str] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.rstrip("\n")
                if line:
                    lines.append(line)
    except OSError as exc:
        return (
            VerifyResult(
                valid=False,
                entries_count=0,
                first_invalid_index=None,
                reason=f"could not read audit log: {exc}",
            ),
            [],
        )
    except UnicodeDecodeError as exc:
        return (
            VerifyResult(
                valid=False,
                entries_count=0,
                first_invalid_index=None,
                reason=f"audit log is not valid UTF-8: {exc}",
            ),
            [],
        )
    return None, lines


def verify_audit_log(
    path: str,
    *,
    hmac_secret: Optional[str] = None,
    require_hmac: bool = False,
) -> VerifyResult:
    """Verify a ForgeLM ``audit_log.jsonl`` chain integrity.

    Mirrors :meth:`AuditLogger.log_event` exactly:

    - Each line is the JSON encoding produced by ``json.dumps(entry, default=str)``
      (no key sorting, no separator overrides).
    - The first entry's ``prev_hash`` must be ``"genesis"``.
    - Every subsequent entry's ``prev_hash`` must equal
      ``sha256(prior_full_line_json).hexdigest()`` — including any ``_hmac``
      field present on the prior line, since the chain is computed over the
      *post-HMAC* line as written.
    - When ``hmac_secret`` is provided, each entry's ``_hmac`` field is
      verified as ``HMAC-SHA256(key, entry_json_without_hmac)`` where
      ``key = sha256(secret + run_id).digest()`` (operator's per-run key).

    Args:
        path: Path to the ``audit_log.jsonl`` file.
        hmac_secret: Optional operator secret. When provided, HMAC tags on
            each line are verified. Lines lacking an ``_hmac`` field are
            tolerated (the writer omits the field when no secret is set)
            unless ``require_hmac=True``.
        require_hmac: When ``True``, every entry must carry a valid
            ``_hmac`` field — a missing tag fails verification. Requires a
            non-empty ``hmac_secret``: ``require_hmac=True`` with
            ``hmac_secret=None`` returns ``valid=False`` rather than
            silently degrading to a presence-only check. Used by the
            CLI's ``--require-hmac`` flag for strict enterprise audits.

    Returns:
        :class:`VerifyResult`. ``valid=True`` only when the chain is intact
        end-to-end (and HMAC tags pass when a secret was supplied / required).

    Notes:
        Reads the log line-by-line (streaming) so RAM usage stays
        bounded for large logs. Genesis-manifest sidecar
        (``<path>.manifest.json``) is checked when present.
    """
    # ``require_hmac`` without a secret cannot authenticate anything: the
    # per-entry check below would only confirm an ``_hmac`` tag is *present*,
    # never that it is *valid*, so strict mode would silently degrade to a
    # presence check and return valid=True on a forged log. The CLI seam
    # already refuses this combination (``_verify_audit.py``); enforce the
    # same contract at the library boundary so notebook/SDK callers cannot
    # get a fail-open pass (F-P4-OPUS-03).
    if require_hmac and not hmac_secret:
        return VerifyResult(
            valid=False,
            entries_count=0,
            first_invalid_index=None,
            reason="require_hmac=True requires a non-empty hmac_secret to authenticate _hmac tags",
        )
    failure, lines = _read_audit_log_lines(path)
    if failure is not None:
        return failure
    if not lines:
        # An empty log is legitimate ONLY when no genesis manifest pins a
        # non-empty first entry. A manifest present + empty log is the
        # truncate-to-empty attack the manifest exists to detect, so consult it
        # before reporting a clean empty chain (previously this early-returned
        # valid=True unconditionally — F-P4-OPUS-01).
        manifest_failure = _verify_genesis_manifest(path, None, None, 0)
        if manifest_failure is not None:
            return manifest_failure
        return VerifyResult(valid=True, entries_count=0)

    chain_result = _verify_chain_walk(lines, hmac_secret, require_hmac)
    if not chain_result.valid:
        return chain_result

    # Re-parse line 1 to capture first_run_id / first_line_hash for the
    # manifest cross-check. Cheaper than threading state out of the walk.
    try:
        first_entry = json.loads(lines[0])
    except json.JSONDecodeError:
        # Should be unreachable — _verify_chain_walk already accepted line 1.
        return chain_result
    first_run_id = first_entry.get("run_id")
    first_line_hash = hashlib.sha256(lines[0].encode("utf-8")).hexdigest()

    manifest_failure = _verify_genesis_manifest(path, first_run_id, first_line_hash, len(lines))
    if manifest_failure is not None:
        return manifest_failure

    return chain_result


# ---------------------------------------------------------------------------
# Phase 14 — Pipeline manifest (chain-level Annex IV artefact)
# ---------------------------------------------------------------------------
#
# The pipeline manifest is the *index* over a multi-stage training run.
# Per-stage ``training_manifest.json`` files (produced by
# :func:`generate_training_manifest` + :func:`export_compliance_artifacts`)
# remain individually valid against the existing single-stage Annex IV
# schema; the pipeline manifest ties them together at the chain level so
# auditors can verify both the per-stage evidence AND the chain
# integrity that connects the records.
#
# Lives in ``compliance.py`` (alongside the single-stage manifest) so
# Annex IV schema decisions live in one module.  The orchestrator
# imports these functions from here and never touches the schema
# directly.


def _provider_metadata_from_config(root_cfg: Any) -> Dict[str, Any]:
    """Extract the ``annex_iv`` and ``risk_assessment`` provider metadata
    from a root :class:`ForgeConfig`, in the shape the pipeline manifest
    embeds verbatim.

    Defensive — both blocks are optional; an absent block produces an
    absent key in the returned dict rather than a half-populated record.
    """
    payload: Dict[str, Any] = {}
    comp_cfg = getattr(root_cfg, "compliance", None)
    if comp_cfg:
        payload["annex_iv"] = {
            "provider_name": comp_cfg.provider_name,
            "provider_contact": comp_cfg.provider_contact,
            "system_name": comp_cfg.system_name,
            "intended_purpose": comp_cfg.intended_purpose,
            "known_limitations": comp_cfg.known_limitations,
            "system_version": comp_cfg.system_version,
            "risk_classification": comp_cfg.risk_classification,
        }
    risk_cfg = getattr(root_cfg, "risk_assessment", None)
    if risk_cfg:
        payload["risk_assessment"] = {
            "intended_use": risk_cfg.intended_use,
            "foreseeable_misuse": risk_cfg.foreseeable_misuse,
            "risk_category": risk_cfg.risk_category,
            "mitigation_measures": risk_cfg.mitigation_measures,
            "vulnerable_groups_considered": risk_cfg.vulnerable_groups_considered,
        }
    return payload


def generate_pipeline_manifest(state: Any, root_cfg: Any) -> Dict[str, Any]:
    """Build the chain-level Annex IV manifest.

    Accepts the orchestrator's :class:`PipelineState` (any object
    exposing the same field names — duck-typed to avoid a circular
    import from :mod:`forgelm.cli._pipeline`) and the root
    :class:`ForgeConfig`.  Returns a JSON-serialisable dict matching the
    schema documented in
    ``docs/roadmap/phase-14-pipeline-chains.md`` Task 3.

    The per-stage rows are taken from ``state.stages`` verbatim — the
    on-disk state file and the manifest carry the same stage payload, so
    a reviewer can correlate the two without translating.  Provider /
    risk metadata is copied from the root config (immutable across the
    pipeline run by the per-stage inheritance matrix).
    """
    stages_payload: List[Dict[str, Any]] = []
    for s in state.stages:
        stages_payload.append(
            {
                "name": s.name,
                "index": s.index,
                "trainer_type": s.trainer_type,
                "status": s.status,
                "input_model": s.input_model,
                "input_source": s.input_source,
                "output_model": s.output_model,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "duration_seconds": s.duration_seconds,
                "training_manifest": s.training_manifest,
                "metrics": dict(s.metrics or {}),
                "gate_decision": s.gate_decision,
                "auto_revert_triggered": bool(s.auto_revert_triggered),
                "skipped_reason": s.skipped_reason,
                "exit_code": s.exit_code,
                "error": s.error,
            }
        )

    manifest: Dict[str, Any] = {
        "forgelm_version": state.forgelm_version,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pipeline_run_id": state.pipeline_run_id,
        "pipeline_config_hash": state.pipeline_config_hash,
        "started_at": state.started_at,
        "finished_at": state.finished_at,
        "final_status": state.final_status,
        "stopped_at": state.stopped_at,
        "stages": stages_payload,
    }
    manifest.update(_provider_metadata_from_config(root_cfg))
    # Stamp a content hash over the whole manifest (minus the metadata
    # block) so the verifier can detect post-generation edits to stage
    # metrics / gate_decision / output_model that keep the chain links
    # self-consistent.  Reuses the single-stage Annex IV canonicalisation
    # so both manifests share one algorithm.  pipeline_config_hash only
    # binds the config *inputs*, not the per-stage result payload, so it
    # cannot stand in for this (F-P4-OPUS-20).
    manifest["metadata"] = {"manifest_hash": compute_annex_iv_manifest_hash(manifest)}
    return manifest


def export_pipeline_manifest(manifest: Dict[str, Any], pipeline_output_dir: str) -> str:
    """Write *manifest* to ``<pipeline_output_dir>/compliance/pipeline_manifest.json``.

    Atomic write via tmp file + ``os.replace`` so a partial write on
    SIGKILL leaves the previous valid manifest intact — the orchestrator
    calls this on every stage transition; an interrupted transition must
    not corrupt the artefact.
    """
    target_dir = os.path.join(pipeline_output_dir, "compliance")
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, "pipeline_manifest.json")
    tmp_path = target_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, default=str)
        # Flush userspace buffer then sync to storage before the rename so the
        # artefact survives a kernel crash or OOM-kill between file-close and
        # os.replace.  Mirrors the fsync discipline in log_event (Article 12
        # durability requirement; F-M-12).
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target_path)
    return target_path


def verify_pipeline_manifest(manifest: Dict[str, Any]) -> List[str]:
    """Public Annex IV verifier for a pipeline manifest *dict*.

    Returns a list of human-readable violation strings; an empty list
    means the manifest is structurally and chain-consistently valid.
    See :func:`_verify_manifest_payload` for the complete check matrix.

    Library callers (test harnesses, internal integrity checks) consume
    this directly; the CLI command ``forgelm verify-annex-iv --pipeline
    <dir>`` reads the file via :func:`verify_pipeline_manifest_at_path`,
    which adds the disk-bound checks (per-stage training_manifest
    existence) on top.
    """
    return _verify_manifest_payload(manifest)


def verify_pipeline_manifest_at_path(pipeline_dir: str) -> List[str]:
    """Disk-backed wrapper around :func:`verify_pipeline_manifest`.

    Reads ``<pipeline_dir>/compliance/pipeline_manifest.json``, runs the
    in-memory verifier on the parsed payload, then layers on disk-only
    checks (per-stage ``training_manifest`` file existence).  Pre-flight
    failures (missing manifest file, malformed JSON) surface as a single-
    entry violation list so the CLI's exit-code mapping is uniform.

    Violation strings may carry a leading routing token —
    :data:`PIPELINE_MANIFEST_IO_ERROR_PREFIX` for an OSError-shaped read
    failure, :data:`PIPELINE_MANIFEST_INPUT_ERROR_PREFIX` for a missing or
    unparseable manifest.  Untagged violations are integrity findings
    (structural, chain, missing per-stage evidence).  Callers that display
    violations must strip the tokens; callers that route on them must match
    the exact prefix rather than any free-text substring (F-P4-OPUS-25).
    """
    manifest_path = os.path.join(pipeline_dir, "compliance", "pipeline_manifest.json")
    if not os.path.isfile(manifest_path):
        return [f"{PIPELINE_MANIFEST_INPUT_ERROR_PREFIX}pipeline_manifest.json not found at {manifest_path}"]
    # Phase 14 post-release review: separate the two failure modes via
    # distinct sentinel prefixes so the CLI can map them to the right
    # exit code (mirrors the single-artifact verifier in
    # _verify_annex_iv.py — FileNotFoundError / JSONDecodeError →
    # EXIT_CONFIG_ERROR (1) (operator-actionable input), OSError →
    # EXIT_TRAINING_ERROR (2) (genuine runtime I/O failure on a
    # reachable path)).
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except json.JSONDecodeError as e:
        return [f"{PIPELINE_MANIFEST_INPUT_ERROR_PREFIX}pipeline_manifest.json invalid JSON: {e}"]
    except OSError as e:
        return [f"{PIPELINE_MANIFEST_IO_ERROR_PREFIX}pipeline_manifest.json unreadable: {e}"]

    violations: List[str] = list(_verify_manifest_payload(manifest))

    # Disk-only check: each completed stage's training_manifest must
    # exist.  The in-memory verifier cannot see the filesystem.
    # Phase 14 review-response: type-guard each stage entry so a
    # tampered manifest (``stages: [null, "foo"]``) surfaces as a
    # violation rather than raising ``AttributeError`` on ``s.get``.
    raw_stages = manifest.get("stages")
    if not isinstance(raw_stages, list):
        # In-memory verifier already flagged this; nothing more to
        # check on disk.
        return violations
    for idx, s in enumerate(raw_stages):
        if not isinstance(s, dict):
            violations.append(f"stage at index {idx} is not an object (got {type(s).__name__})")
            continue
        name = s.get("name", "<unnamed>")
        per_stage_manifest = s.get("training_manifest")
        if per_stage_manifest and s.get("status") == "completed" and not os.path.isfile(per_stage_manifest):
            violations.append(f"Stage {name!r}: training_manifest at {per_stage_manifest!r} is missing.")

    return violations
