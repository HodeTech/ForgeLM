"""``forgelm approve`` and ``forgelm reject`` dispatchers (Article 14).

The two commands share the audit-event helper set (resolve approver, find
the matching ``human_approval.required`` event, load training metrics from
the on-disk manifest, build a webhook notifier from the co-located config)
and the ``_run_*_cmd`` dispatchers themselves. Co-locating them avoids
splitting cohesive helpers across files.
"""

from __future__ import annotations

import json
import os
import sys
import types
from typing import Any, Dict, NoReturn, Optional

import yaml

from .._exit_codes import EXIT_CONFIG_ERROR, EXIT_TRAINING_ERROR
from .._logging import logger

_STAGING_SUFFIX = ".staging"

# Audit event vocabulary for the human-approval gate.  Centralised so a future
# rename or typo cannot drift across the registry, the emitter call sites, and
# the guard that blocks double decisions.
_EVT_HUMAN_APPROVAL_GRANTED = "human_approval.granted"
_EVT_HUMAN_APPROVAL_REJECTED = "human_approval.rejected"


def _staging_path_inside_output_dir(staging_path: str, output_dir: str) -> bool:
    """Return True iff ``staging_path`` resolves inside ``output_dir``.

    Defence-in-depth against a tampered audit log: ``staging_path`` is read
    from ``audit_log.jsonl`` which is HMAC-signed only when
    ``FORGELM_AUDIT_SECRET`` is set.  Without the HMAC, an attacker who can
    rewrite the log could plant an absolute or ``..``-traversing path; this
    helper rejects anything whose realpath escapes the operator-supplied
    ``output_dir``.  Symlinks are honoured (``realpath`` follows them) so a
    legitimate symlink whose target lives inside ``output_dir`` still
    validates — only paths that *escape* the boundary are blocked.
    """
    real_output = os.path.realpath(output_dir)
    real_staging = os.path.realpath(staging_path)
    try:
        return os.path.commonpath([real_output, real_staging]) == real_output
    except ValueError:
        # commonpath raises ValueError when the paths live on different
        # drives (Windows) — treat as out-of-bounds.
        return False


def _resolve_approver_identity() -> str:
    """Resolve the operator identity for an approve/reject audit entry.

    Mirrors :class:`forgelm.compliance.AuditLogger`'s operator resolution so
    a `human_approval.granted` / `human_approval.rejected` event identifies
    the human exactly the way pre-existing pipeline events do:

    1. ``FORGELM_OPERATOR`` env var (highest priority — explicit operator
       identification, used in CI/CD and shared workstation setups).
    2. ``getpass.getuser()`` (the OS-reported username; falls back to the
       ``USER`` / ``USERNAME`` env var on its own).
    3. If both fail, refuse to proceed unless the operator explicitly opts
       in via ``FORGELM_ALLOW_ANONYMOUS_OPERATOR=1`` — then the identity
       becomes ``anonymous@<hostname>``.  Loud failure beats silently
       writing an unattributed Article 12 record-keeping event.

    Pulled out so the approve/reject handlers don't reach into AuditLogger's
    constructor logic and so the test harness has a single hook to monkey-patch.
    """
    explicit = os.getenv("FORGELM_OPERATOR")
    if explicit:
        return explicit
    try:
        import getpass

        return getpass.getuser()
    except (KeyError, OSError, ImportError):
        pass
    # Mirrors AuditLogger: refuse anonymous identity unless the operator opts in.
    allow_anonymous = os.getenv("FORGELM_ALLOW_ANONYMOUS_OPERATOR") == "1"
    if not allow_anonymous:
        import sys

        logger.error(
            "Operator identity unavailable: no FORGELM_OPERATOR set and "
            "getpass.getuser() failed. Set FORGELM_OPERATOR=<id> for CI/CD "
            "pipelines, or FORGELM_ALLOW_ANONYMOUS_OPERATOR=1 to opt in to "
            "anonymous audit entries (not recommended for EU AI Act Article 12)."
        )
        sys.exit(EXIT_CONFIG_ERROR)
    import socket

    try:
        hostname = socket.gethostname() or "unknown-host"
    except OSError:
        # gethostname() can raise OSError in restricted container / sandbox
        # environments where the hostname is not resolvable.  The whole
        # point of FORGELM_ALLOW_ANONYMOUS_OPERATOR=1 is to keep running in
        # exactly those environments, so swallow the failure narrowly.
        hostname = "unknown-host"
    return f"anonymous@{hostname}"


def _find_human_approval_required_event(audit_log_path: str, run_id: str) -> Optional[dict]:
    """Return the most-recent ``human_approval.required`` event for *run_id*.

    Wave 2a Round-1 review consolidated the audit-log JSONL parser into
    :mod:`._audit_log_reader` so a future malformed-line policy fix lands
    in one place.  This helper now delegates; the original line-by-line
    streaming + skipped-line warning behaviour is preserved exactly
    (verified by the existing approve / reject test suites).
    """
    from ._audit_log_reader import find_latest_event_for_run

    return find_latest_event_for_run(
        audit_log_path,
        run_id=run_id,
        matches=lambda e: e.get("event") == "human_approval.required",
    )


_TERMINAL_DECISION_EVENTS = frozenset({_EVT_HUMAN_APPROVAL_GRANTED, _EVT_HUMAN_APPROVAL_REJECTED})


def _find_human_approval_decision_event(audit_log_path: str, run_id: str) -> Optional[dict]:
    """Return the most-recent terminal decision event for *run_id*, or None.

    A terminal decision is either ``human_approval.granted`` or
    ``human_approval.rejected``. Finding one before attempting promotion
    prevents double-approve and approve-after-reject races.

    Same Wave 2a Round-1 consolidation as
    :func:`_find_human_approval_required_event` — delegates to the shared
    :mod:`._audit_log_reader` so the malformed-line policy lives in
    one place.
    """
    from ._audit_log_reader import find_latest_event_for_run

    return find_latest_event_for_run(
        audit_log_path,
        run_id=run_id,
        matches=lambda e: e.get("event") in _TERMINAL_DECISION_EVENTS,
    )


def _atomic_rename_or_move(src: str, dst: str) -> str:
    """Atomically promote *src* → *dst*.

    Tries ``os.rename`` first — atomic on the same filesystem and the only
    operation that meaningfully prevents a concurrent ``forgelm approve``
    on the same staging directory from racing. Falls back to ``shutil.move``
    on ``OSError(EXDEV)`` so cross-device output mounts keep working.

    Returns the strategy used (``"rename"`` or ``"move"``) so the caller
    can record it in the audit event.
    """
    import errno
    import shutil

    try:
        os.rename(src, dst)
        return "rename"
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        logger.debug(
            "Staging directory %s and final directory %s live on different filesystems; "
            "falling back to shutil.move (copy + delete). The promotion is no longer "
            "atomic, but the staging artefacts are preserved on copy failure.",
            src,
            dst,
        )
        shutil.move(src, dst)
        return "move"


def _load_metrics_from_manifest(output_dir: str) -> dict:
    """Read final metrics from ``compliance/training_manifest.yaml`` if present."""
    manifest_path = os.path.join(output_dir, "compliance", "training_manifest.yaml")
    if not os.path.isfile(manifest_path):
        return {}
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Could not read training manifest at %s: %s", manifest_path, exc)
        return {}
    # ``yaml.safe_load`` happily returns a list or scalar at the document root
    # if a hand-edited or corrupt manifest reshapes the top level; guard the
    # ``.get`` call so an unexpected root type degrades to empty metrics with a
    # warning instead of raising AttributeError into the approval dispatcher.
    if not isinstance(manifest, dict):
        logger.warning(
            "Unexpected manifest root type %s at %s; expected a YAML mapping.",
            type(manifest).__name__,
            manifest_path,
        )
        return {}
    metrics = manifest.get("final_metrics", {})
    return metrics if isinstance(metrics, dict) else {}


def _build_approval_notifier(output_dir: str):
    """Construct a WebhookNotifier from a co-located forgelm config, if any.

    Approve / reject runs do not require ``--config``; the operator just
    points at the training output directory. We look for the webhook config
    inside ``<output_dir>/compliance/compliance_report.json``; if it is not
    there the notifier returns a no-op ``WebhookNotifier`` whose
    ``_resolve_url`` yields ``None`` so the gate still completes cleanly.
    """
    from ...webhook import WebhookNotifier

    class _Carrier:
        def __init__(self, webhook_cfg):
            # WebhookNotifier accesses self.config (= config.webhook) via attribute
            # lookup (.url, .notify_on_success, etc.). The co-located config is a
            # plain JSON dict, so convert it to a SimpleNamespace before handing it
            # to WebhookNotifier to avoid AttributeError on dict-style values.
            if isinstance(webhook_cfg, dict):
                self.webhook = types.SimpleNamespace(**webhook_cfg)
            else:
                self.webhook = webhook_cfg  # None → _resolve_url returns None cleanly

    config_path = os.path.join(output_dir, "compliance", "compliance_report.json")
    webhook_cfg = None
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as fh:
                report = json.load(fh)
            if isinstance(report, dict):
                raw_cfg = report.get("webhook_config")
                webhook_cfg = raw_cfg if isinstance(raw_cfg, dict) else None
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("Could not load co-located webhook config from %s: %s", config_path, exc)
    return WebhookNotifier(_Carrier(webhook_cfg))


def _output_error_and_exit(output_format: str, msg: str, exit_code: int) -> NoReturn:
    """Emit *msg* as a structured JSON error or a log record, then exit.

    ``-> NoReturn`` (Wave 2a Round-2 review nit): mypy / pyright otherwise
    treat callers as if control could continue past this helper, producing
    spurious "possibly-unbound variable" warnings for ``required_event`` /
    ``decision_event`` further down ``_run_approve_cmd`` /
    ``_run_reject_cmd``.  ``sys.exit`` raises ``SystemExit`` so this never
    returns; pinning the type makes the contract visible to the typechecker.
    """
    if output_format == "json":
        print(json.dumps({"success": False, "error": msg}))
    else:
        logger.error(msg)
    sys.exit(exit_code)


def _assert_audit_log_readable_or_exit(audit_log_path: str, output_format: str) -> None:
    """Wave 2a Round-5 (F-R5-01) readability gate shared across the
    approve / reject / approvals family.

    A chmod-broken audit log otherwise reaches ``iter_audit_events`` →
    OSError-on-open is logged + swallowed → callers see "no events for
    this run" (wrong debugging path on the Article 14 critical path).
    Surface the chmod / mount issue with an actionable message instead.
    Caller passes the file path; this helper short-circuits via
    ``_output_error_and_exit`` (which is ``-> NoReturn``) when the file
    exists but is unreadable.  Missing-file is the caller's responsibility
    (the dispatchers each have their own missing-log policy).
    """
    from ._audit_log_reader import is_audit_log_readable

    if os.path.isfile(audit_log_path) and not is_audit_log_readable(audit_log_path):
        _output_error_and_exit(
            output_format,
            f"Audit log {audit_log_path!r} exists but is not readable. "
            "Check filesystem permissions (chmod / mount opts) and re-run.",
            EXIT_CONFIG_ERROR,
        )


def _read_required_event_for_reject(
    audit_log_path: str,
    run_id: str,
    output_format: str,
) -> Dict[str, Any]:
    """Reject-flavoured twin of :func:`_read_required_event_for_approve`.

    Pulled out of :func:`_run_reject_cmd` for the same SonarCloud S3776
    cognitive-complexity reason as the approve helper — adding the
    Round-5 readability gate pushed reject's inline body over 15.  The
    operator copy ("record a rejection" / "re-rejection is not allowed")
    differs from approve's ("promote" / "re-approve"), so the two
    helpers stay separate rather than ballooning the parameter list.
    """
    from forgelm import cli as _cli_facade

    from ._audit_log_reader import AuditLogParseError

    try:
        required_event = _cli_facade._find_human_approval_required_event(audit_log_path, run_id)
    except AuditLogParseError as exc:
        _output_error_and_exit(
            output_format,
            f"Audit log {audit_log_path!r} is corrupted at line {exc.line_number} ({exc.reason}). "
            "Refusing to record a rejection — repair or rotate the audit log first.",
            EXIT_CONFIG_ERROR,
        )
    if required_event is None:
        _output_error_and_exit(
            output_format,
            f"No human_approval.required event for run_id={run_id!r} found in {audit_log_path!r}. "
            "Refusing to record a rejection on a run that did not request one.",
            EXIT_CONFIG_ERROR,
        )

    try:
        decision_event = _cli_facade._find_human_approval_decision_event(audit_log_path, run_id)
    except AuditLogParseError as exc:
        _output_error_and_exit(
            output_format,
            f"Audit log {audit_log_path!r} is corrupted at line {exc.line_number} ({exc.reason}). "
            "Refusing to record a rejection — repair or rotate the audit log first.",
            EXIT_CONFIG_ERROR,
        )
    if decision_event is not None:
        prior = decision_event.get("event", "unknown")
        _output_error_and_exit(
            output_format,
            f"Run {run_id!r} already has a terminal decision ({prior!r}). "
            "Refusing to record another decision — re-rejection is not allowed.",
            EXIT_CONFIG_ERROR,
        )
    return required_event


def _read_required_event_for_approve(
    audit_log_path: str,
    run_id: str,
    output_format: str,
) -> Dict[str, Any]:
    """Read the ``human_approval.required`` event for ``run_id`` and
    enforce no-prior-terminal-decision (approve flavour).

    Exits via :func:`_output_error_and_exit` on parse error, missing
    required event, or a pre-existing terminal decision.  Pulled out of
    :func:`_run_approve_cmd` so the dispatcher stays under SonarCloud
    S3776 cognitive-complexity ceiling.  Reject has its own slightly-
    different operator copy (``"record a rejection"``) and stays inline
    in :func:`_run_reject_cmd` — sharing the helper would either dilute
    the operator messages or balloon the helper's parameter list.
    """
    from forgelm import cli as _cli_facade

    from ._audit_log_reader import AuditLogParseError

    try:
        required_event = _cli_facade._find_human_approval_required_event(audit_log_path, run_id)
    except AuditLogParseError as exc:
        _output_error_and_exit(
            output_format,
            f"Audit log {audit_log_path!r} is corrupted at line {exc.line_number} ({exc.reason}). "
            "Refusing to promote — repair or rotate the audit log first.",
            EXIT_CONFIG_ERROR,
        )
    if required_event is None:
        _output_error_and_exit(
            output_format,
            f"No human_approval.required event for run_id={run_id!r} found in {audit_log_path!r}. "
            "Refusing to promote — verify the run_id matches the original training run.",
            EXIT_CONFIG_ERROR,
        )

    try:
        decision_event = _cli_facade._find_human_approval_decision_event(audit_log_path, run_id)
    except AuditLogParseError as exc:
        _output_error_and_exit(
            output_format,
            f"Audit log {audit_log_path!r} is corrupted at line {exc.line_number} ({exc.reason}). "
            "Refusing to promote — repair or rotate the audit log first.",
            EXIT_CONFIG_ERROR,
        )
    if decision_event is not None:
        prior = decision_event.get("event", "unknown")
        _output_error_and_exit(
            output_format,
            f"Run {run_id!r} already has a terminal decision ({prior!r}). "
            "Refusing to promote — re-approve is not allowed.",
            EXIT_CONFIG_ERROR,
        )
    return required_event


def _run_approve_cmd(args, output_format: str) -> None:
    """Promote ``final_model.staging/`` → ``final_model/`` after human review."""
    # Late import via the package facade so monkeypatched
    # ``forgelm.cli._build_approval_notifier`` references resolve correctly.
    from forgelm import cli as _cli_facade

    output_dir = args.output_dir
    run_id = args.run_id
    audit_log_path = os.path.join(output_dir, "audit_log.jsonl")

    _assert_audit_log_readable_or_exit(audit_log_path, output_format)
    # Strict-mode parsing (Wave 2a Round-2 hardening): a corrupted decision
    # record that gets silently skipped looks identical to "no approval yet",
    # which would let an operator double-grant. ``_read_required_event_for_approve``
    # converts AuditLogParseError into an actionable EXIT_CONFIG_ERROR so
    # the operator fixes the log first.
    required_event = _read_required_event_for_approve(audit_log_path, run_id, output_format)

    staging_path = required_event.get("staging_path") or os.path.join(output_dir, f"final_model{_STAGING_SUFFIX}")
    # Defence-in-depth: refuse a staging_path that escapes output_dir (a
    # tampered audit log without HMAC signing could otherwise plant an
    # absolute path or ``..`` traversal).
    if not _staging_path_inside_output_dir(staging_path, output_dir):
        _output_error_and_exit(
            output_format,
            f"Refusing to act on staging_path {staging_path!r}: it resolves outside output_dir {output_dir!r}.",
            EXIT_CONFIG_ERROR,
        )
    # Derive final_path by stripping the staging suffix (and any runtime suffix
    # appended after it, such as ".<run_id>") from staging_path. rfind locates
    # the last occurrence of _STAGING_SUFFIX so "final_model.staging.abc123"
    # correctly yields "final_model" regardless of any trailing run_id segment.
    _idx = staging_path.rfind(_STAGING_SUFFIX)
    final_path = staging_path[:_idx] if _idx != -1 else staging_path

    # Path-existence guards: lexists/islink instead of isdir alone so a broken
    # symlink (target deleted but link kept) surfaces a sensible message rather
    # than the misleading "not found" string.  Mirrors the final_path guard
    # below for consistency.
    if not (os.path.isdir(staging_path) or os.path.islink(staging_path)):
        _output_error_and_exit(
            output_format,
            f"Staging directory not found at {staging_path!r}. "
            "Either the run did not exit with code 4, or it was already approved/cleaned up.",
            EXIT_CONFIG_ERROR,
        )

    if os.path.lexists(final_path):
        _output_error_and_exit(
            output_format,
            f"Cannot promote: final directory already exists at {final_path!r}. Move or delete it first.",
            EXIT_CONFIG_ERROR,
        )

    from ...compliance import AuditLogger
    from ...config import ConfigError

    # Construct the audit logger BEFORE the atomic rename.  If we promoted
    # first and ``AuditLogger.__init__`` then raised (e.g. CI/container env
    # with no resolvable operator identity), the model would already be on
    # disk at ``final_path`` with no corresponding ``human_approval.granted``
    # event — an audit gap that breaks Article 12 record-keeping.  Validating
    # operator identity up front means the gate either succeeds with both
    # promotion and audit, or fails with neither.
    try:
        audit = AuditLogger(output_dir, run_id=run_id)
    except ConfigError as exc:
        _output_error_and_exit(output_format, str(exc), EXIT_CONFIG_ERROR)

    try:
        promote_strategy = _cli_facade._atomic_rename_or_move(staging_path, final_path)
    except OSError as exc:
        _output_error_and_exit(
            output_format,
            f"Failed to promote {staging_path!r} → {final_path!r}: {exc}",
            EXIT_TRAINING_ERROR,
        )

    # ``approver`` records the human who ran ``forgelm approve``; this is a
    # complement to ``audit.operator`` (the FORGELM_OPERATOR-pinned identity
    # carried on every event for HMAC scope and chain attribution).  When
    # FORGELM_OPERATOR is set both fields collapse to the same value; on a
    # shared workstation they intentionally diverge so the audit answers
    # "which pipeline ran this" *and* "which human approved it".
    approver = _cli_facade._resolve_approver_identity()
    audit.log_event(
        _EVT_HUMAN_APPROVAL_GRANTED,
        gate="final_model",
        run_id=run_id,
        approver=approver,
        comment=args.comment or "",
        promote_strategy=promote_strategy,
    )

    metrics = _cli_facade._load_metrics_from_manifest(output_dir)
    notifier = _cli_facade._build_approval_notifier(output_dir)
    run_name = os.path.basename(os.path.normpath(output_dir)) or "approved"
    # Best-effort: the model is already promoted and the granted event already
    # committed.  A webhook is a notification, not a gate — a notifier failure
    # must never abort the approve command (public exit-code contract).
    try:
        notifier.notify_success(run_name=run_name, metrics=metrics)
    except Exception as exc:  # noqa: BLE001 — webhook is best-effort; see comment above
        logger.warning("Approve webhook notification failed (non-fatal): %s", exc)

    if output_format == "json":
        print(
            json.dumps(
                {
                    "success": True,
                    "run_id": run_id,
                    "approver": approver,
                    "final_model_path": final_path,
                    "promote_strategy": promote_strategy,
                },
                indent=2,
            )
        )
    else:
        logger.info("Approved run %s; final model promoted to %s.", run_id, final_path)


def _run_reject_cmd(args, output_format: str) -> None:
    """Record a human-approval rejection (preserves staging directory)."""
    from forgelm import cli as _cli_facade

    output_dir = args.output_dir
    run_id = args.run_id
    audit_log_path = os.path.join(output_dir, "audit_log.jsonl")

    _assert_audit_log_readable_or_exit(audit_log_path, output_format)
    # Strict-mode parsing: surface audit-log corruption to the operator
    # rather than skip the line and produce a misleading "no decision yet"
    # result.  See _read_required_event_for_reject for the same hardening
    # pattern as approve.
    required_event = _read_required_event_for_reject(audit_log_path, run_id, output_format)

    staging_path = required_event.get("staging_path") or os.path.join(output_dir, f"final_model{_STAGING_SUFFIX}")

    # Same defence-in-depth check as the approve handler: refuse a
    # staging_path that escapes output_dir.
    if not _staging_path_inside_output_dir(staging_path, output_dir):
        _output_error_and_exit(
            output_format,
            f"Refusing to act on staging_path {staging_path!r}: it resolves outside output_dir {output_dir!r}.",
            EXIT_CONFIG_ERROR,
        )

    # See approve handler for the islink rationale (broken-symlink edge case).
    if not (os.path.isdir(staging_path) or os.path.islink(staging_path)):
        _output_error_and_exit(
            output_format,
            f"Staging directory not found at {staging_path!r}. Nothing to reject.",
            EXIT_CONFIG_ERROR,
        )

    from ...compliance import AuditLogger
    from ...config import ConfigError

    # See approve handler — same operator-identity ConfigError contract.
    try:
        audit = AuditLogger(output_dir, run_id=run_id)
    except ConfigError as exc:
        _output_error_and_exit(output_format, str(exc), EXIT_CONFIG_ERROR)
    approver = _cli_facade._resolve_approver_identity()
    audit.log_event(
        _EVT_HUMAN_APPROVAL_REJECTED,
        gate="final_model",
        run_id=run_id,
        approver=approver,
        comment=args.comment or "",
        staging_path=staging_path,
    )

    notifier = _cli_facade._build_approval_notifier(output_dir)
    run_name = os.path.basename(os.path.normpath(output_dir)) or "rejected"
    reason = f"{_EVT_HUMAN_APPROVAL_REJECTED}: {args.comment}" if args.comment else _EVT_HUMAN_APPROVAL_REJECTED
    # Best-effort (see approve handler): the rejection is already recorded in the
    # audit log; a webhook hiccup must not abort the reject command.
    try:
        notifier.notify_failure(run_name=run_name, reason=reason)
    except Exception as exc:  # noqa: BLE001 — webhook is best-effort; see comment above
        logger.warning("Reject webhook notification failed (non-fatal): %s", exc)

    if output_format == "json":
        print(
            json.dumps(
                {
                    "success": True,
                    "run_id": run_id,
                    "approver": approver,
                    "staging_path": staging_path,
                    "comment": args.comment or "",
                },
                indent=2,
            )
        )
    else:
        logger.info(
            "Rejected run %s; staging directory preserved at %s for forensic review.",
            run_id,
            staging_path,
        )
