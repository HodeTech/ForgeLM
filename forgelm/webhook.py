import json
import logging
import os
from typing import Any, Dict, FrozenSet, Optional, Tuple, Type
from urllib.parse import urlparse

import requests

from ._http import HttpSafetyError, _mask_netloc, safe_post

# Public re-export surface.  Wave 3 / Faz 28 (C-54) cleanup: dropped
# the ``_is_private_destination`` re-export.  The Phase 7 split moved
# the helper into ``forgelm._http``; external callers / tooling that
# need the SSRF guard import it from there directly.  No downstream
# importer of the webhook-side re-export was found at the time of
# removal, so this is a clean drop (no DeprecationWarning shim).
__all__ = ["HttpSafetyError", "WebhookNotifier", "safe_post"]

logger = logging.getLogger("forgelm.webhook")

# ---------------------------------------------------------------------------
# Outbound payload allowlist (F-PR54-M11)
# ---------------------------------------------------------------------------
#
# ``WebhookNotifier._send`` accepts ``**extra`` so the ``notify_pipeline_*``
# methods can attach event-specific fields without widening a fixed signature
# (see the ``_send`` docstring for why the fixed signature was abandoned in
# Phase 14).  ``**extra`` is an unbounded funnel into an outbound HTTP body:
# whatever a caller passes leaves the process.  Every caller today is
# orchestrator-internal and passes orchestrator-derived literals, so this is
# hygiene rather than a live leak — the risk is a *future* caller threading
# user- or config-derived text through ``_send`` and having it land on a
# third-party receiver (Slack/Teams persist and index message bodies).
#
# The allowlist below is the complete set of extra keys the shipped
# ``notify_*`` methods pass.  ``tests/test_webhook.py`` drives every notifier
# and asserts the two stay in sync, so adding a key without registering it
# fails CI rather than silently dropping a field in production.
_ALLOWED_EXTRA_PAYLOAD_KEYS: FrozenSet[str] = frozenset(
    {
        "stage_count",  # pipeline.started   — int, number of stages in the chain
        "final_status",  # pipeline.completed — str, terminal pipeline state
        "stopped_at",  # pipeline.completed — str|None, halting stage name
        "stage_name",  # pipeline.stage_reverted — str, reverting stage name
    }
)

# Scalar types that survive ``json.dumps`` in ``_post_payload``.  A container
# or arbitrary object would raise ``TypeError`` *inside* the notifier, which
# propagates out of ``notify_*`` and crashes an otherwise-successful run at
# its final step — the exact outcome this module's contract forbids
# ("notify_* is never allowed to fail the training run").  Mirrors the
# existing ``safe_metrics`` numeric filter one level up.
_ALLOWED_EXTRA_VALUE_TYPES: Tuple[Type[Any], ...] = (str, int, float, bool)


class WebhookNotifier:
    """Handles sending training status updates to configured webhook endpoints."""

    def __init__(self, config):
        self.config = config.webhook

    def _resolve_url(self) -> Optional[str]:
        """Pick the webhook URL from the config, falling back to url_env."""
        if not self.config:
            return None
        # ``getattr`` rather than ``self.config.url``: the approve/reject
        # dispatchers rebuild this notifier from a co-located JSON manifest via a
        # ``SimpleNamespace`` that need not carry every ``WebhookConfig``
        # attribute.  A bare attribute access raised ``AttributeError`` there
        # *after* the model was already promoted and the granted audit event
        # committed — crashing a successful enterprise run and violating the
        # public exit-code contract.
        url = getattr(self.config, "url", None)
        if not url and getattr(self.config, "url_env", None):
            url = os.getenv(self.config.url_env)
        return url or None

    @staticmethod
    def _mask(url: str) -> str:
        """Redact credentials and signed query params from a webhook URL.

        Thin wrapper over :func:`forgelm._http._mask_netloc` — the single
        URL-masking chokepoint (F-P5-OPUS-11).  Webhook URLs are bearer
        tokens (Slack/Teams/Discord carry the secret in the path or query,
        basic auth embeds it in userinfo, and custom receivers may put the
        token in the *first* path segment).  ``_mask_netloc`` strips the
        entire path/query/userinfo and returns only ``scheme://host`` so no
        secret material reaches operator logs.  The earlier local
        implementation appended the first path segment, which leaked the
        token for custom receivers shaped ``https://host/<TOKEN>``
        (F-P5-OPUS-06).
        """
        return _mask_netloc(url)

    def _post_payload(self, url: str, payload: dict, event: str) -> None:
        """POST *payload* to *url* and log any transport / HTTP errors.

        Delegates SSRF / scheme / TLS / redirect / timeout discipline to
        :func:`forgelm._http.safe_post` so every outbound HTTP call site in
        the codebase shares the same policy. Webhook-specific behaviour kept
        here:

        * The local ``timeout`` variable resolves from
          ``self.config.timeout`` and falls back to
          ``WebhookConfig.model_fields["timeout"].default`` (currently 10s
          per Wave 3 / F-compliance-106 — was 5s historically) when the
          attribute is absent on a hand-rolled config namespace.  Sub-1
          values are clamped to the 1s floor (NOT to the model default —
          see the inline comment around ``timeout < 1`` for the
          F-W3FU-followup framing); 0 / negative budgets are not honoured.
        * On policy rejection or transport error we log a warning and
          *swallow* — ``notify_*`` is never allowed to fail the training run.
        * Response body suppression on non-2xx — receivers (Slack, Teams)
          sometimes echo the payload, which can carry config-derived secrets.

        Signature is part of the internal Notifier contract: Phase 8 adds
        ``notify_reverted`` / ``notify_awaiting_approval`` that call
        ``self._post_payload(url, payload, event)`` — do not rename or
        reorder arguments without coordinating with that work.
        """
        masked_url = self._mask(url)

        # Resolve TLS verify setting. Default True (strict); allow operator
        # to point at a custom CA bundle.
        ca_bundle = getattr(self.config, "tls_ca_bundle", None)

        # Timeout floor — webhook keeps the historical 1s floor (``safe_post``
        # rejects 0/None unconditionally).  Sub-1 values are clamped to
        # the floor (NOT to the model default).  Pre-Wave-3-followup the
        # branch jumped to ``default_timeout`` (10s) on a sub-1 value,
        # which silently 10x'd the operator's chosen budget; the
        # documented contract is "below 1s → clamp to 1s", so we now
        # clamp to the floor literally.  F-W3FU-S-04 also dropped the
        # dead ``isinstance(timeout, (int, float))`` check (Pydantic
        # already enforces the int type at config load).
        from .config import WebhookConfig as _WebhookConfig

        default_timeout = _WebhookConfig.model_fields["timeout"].default
        timeout = getattr(self.config, "timeout", default_timeout)
        if timeout < 1:
            logger.warning(
                "Webhook timeout=%r is below the 1s floor; clamping to 1s.",
                timeout,
            )
            timeout = 1

        allow_private = bool(getattr(self.config, "allow_private_destinations", False))

        try:
            resp = safe_post(
                url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=timeout,
                ca_bundle=ca_bundle,
                allow_private=allow_private,
                # Webhook keeps the documented 1s floor.  ``http://`` is permitted
                # by default (the upstream warning at ``_send`` flags it as
                # plaintext); set ``webhook.require_https: true`` to make the SSRF
                # chokepoint refuse cleartext delivery instead (F-P5-OPUS-12).
                allow_insecure_http=not bool(getattr(self.config, "require_https", False)),
                min_timeout=1.0,
            )
        except HttpSafetyError as exc:
            logger.warning(
                "Refusing to post webhook for event '%s' (url=%s): %s",
                event,
                masked_url,
                exc,
            )
            return
        except ImportError as exc:
            # ``_http._pinned_session`` raises a bare ImportError when
            # ``requests-toolbelt`` (the HTTPS IP-pinning adapter) is missing —
            # e.g. a ``--no-deps`` / vendored / frozen environment.  It is a
            # mandatory base dependency, so this is low-likelihood, but
            # ImportError is NOT a ``requests.RequestException`` subclass and
            # would otherwise propagate straight out of ``notify_*`` and crash
            # an otherwise-successful training run at the final notification
            # step — the exact outcome this module's contract forbids
            # ("notify_* is never allowed to fail the training run").  Log and
            # swallow with the same non-fatal semantics as the transport
            # branches below.
            logger.warning(
                "Webhook dependency unavailable for event '%s' (url=%s): %s",
                event,
                masked_url,
                exc,
            )
            return
        except requests.exceptions.Timeout:
            logger.warning("Webhook request timed out for event '%s' (url=%s).", event, masked_url)
            return
        except requests.exceptions.ConnectionError:
            logger.warning("Webhook connection failed for event '%s' (url=%s).", event, masked_url)
            return
        except requests.RequestException as exc:
            # ``requests.RequestException`` is the base of the library's
            # transport-error hierarchy (Timeout / ConnectionError / SSLError
            # / TooManyRedirects / etc.) so this single catch covers every
            # network-shaped failure after the more-specific clauses above.
            # We deliberately do **not** add a trailing ``except Exception:``
            # — programming bugs (TypeError, ValueError, attribute errors in
            # payload construction) should propagate so they surface in
            # tests rather than being silently absorbed by the webhook path.
            # Because this clause only ever catches an EXPECTED transport
            # error, log at WARNING (no traceback) per
            # logging-observability.md "Webhook notifications" rule 1 — not
            # logger.exception/ERROR, which would mislabel a routine DNS/TLS
            # blip as an "Unexpected error" and inflate alert noise
            # (F-P4-OPUS-31).
            logger.warning(
                "Webhook transport error for event '%s' (url=%s): %s",
                event,
                masked_url,
                exc,
            )
            return

        if not resp.ok:
            # Don't log resp.text — receivers sometimes echo the payload
            # (which can contain secret-bearing fields) or include their
            # own auth context. Surface only the status code.
            logger.warning(
                "Webhook HTTP %d for event '%s' (url=%s) — response body suppressed",
                resp.status_code,
                event,
                masked_url,
            )

    def _send(
        self,
        *,
        event: str,
        run_name: str,
        status: str,
        title: str,
        text: str,
        color: str = "#36a64f",
        metrics: Optional[Dict[str, float]] = None,
        reason: Optional[str] = None,
        model_path: Optional[str] = None,
        **extra: Any,
    ) -> None:
        """Build + post the webhook payload.

        ``**extra`` carries event-specific fields the
        ``notify_pipeline_*`` methods forward (stage_count, final_status,
        stopped_at, stage_name, …) — Phase 14 review-response fix: pre-
        fix the pipeline notifiers passed unknown kwargs to a fixed
        signature, the resulting ``TypeError`` was swallowed by the
        orchestrator's best-effort try/except, and pipeline webhooks
        silently never fired.  Extras are merged into the payload under
        their original key names so existing Slack / Teams receivers
        that pick fields by name keep working.

        ``**extra`` is **not** a free-form passthrough: it is screened by
        :meth:`_screen_extras` against ``_ALLOWED_EXTRA_PAYLOAD_KEYS``
        before anything reaches the wire (F-PR54-M11).  An unregistered
        key is logged and dropped, so a future caller cannot use this
        parameter to funnel user- or config-derived text to a third-party
        receiver by accident.
        """
        url = self._resolve_url()
        if not url:
            return

        # Intentional plaintext-scheme detection: HTTPS is the documented
        # recommendation but plaintext is supported for closed-network
        # receivers, and the SSRF guard in ``forgelm._http`` still applies.
        # The warning below makes the unencrypted path loud in operator logs.
        # Compare the parsed, lower-cased scheme (matching the case-insensitive
        # gate ``_http.safe_post`` enforces via ``urlparse``) so a mixed-case
        # ``HTTP://`` URL — which still routes through the cleartext path — does
        # not silently skip this operator-facing plaintext warning.
        if urlparse(url).scheme.lower() == "http":  # NOSONAR python:S5332
            logger.warning("Webhook URL uses HTTP (not HTTPS). Data will be sent unencrypted.")

        # Sanitize metrics — only include numeric values
        safe_metrics = {k: v for k, v in (metrics or {}).items() if isinstance(v, (int, float))}

        # Generic webhook payload (works for most HTTP receivers).
        # ``model_path`` is included only for ``approval.required`` events;
        # we add the key unconditionally (even as None) to keep the schema
        # stable so downstream consumers can rely on its presence.
        payload: Dict[str, Any] = {
            "event": event,
            "run_name": run_name,
            "status": status,
            "metrics": safe_metrics,
            "reason": reason,
            "model_path": model_path,
            # Slack-compatible formatting (receivers can ignore)
            "attachments": [{"title": title, "text": text, "color": color}],
        }
        # Merge event-specific extras (pipeline.* events carry
        # stage_count / final_status / stopped_at / stage_name) after
        # allowlist + type + secret-masking screening.  Drop any extra
        # whose key collides with a base-payload field so the contract
        # stays stable; we don't expect collisions in practice since the
        # pipeline notifier names are disjoint, but the guard makes the
        # merge order explicit.
        for key, value in self._screen_extras(event, extra).items():
            if key not in payload:
                payload[key] = value

        self._post_payload(url, self._mask_outbound_strings(payload), event)

    @classmethod
    def _mask_outbound_strings(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run every free-text string in *payload* through the secrets masker.

        Single chokepoint, applied last, so no field can reach the wire
        unmasked by being assembled somewhere the per-field masking didn't
        reach.  That gap was real: ``notify_pipeline_reverted`` masked its
        ``stage_name`` field but interpolated the *raw* stage name into the
        Slack ``attachments[0].text`` prose two lines later, so a
        secret-shaped stage name shipped anyway.  Masking the assembled
        payload instead of the individual arguments makes that class of
        mistake structurally impossible.

        ``event``, ``status`` and ``color`` are deliberately exempt: each is
        a closed set of code literals chosen by ``notify_*`` itself, never
        operator- or config-derived, and receivers route on them.  Everything
        else — ``run_name``, ``reason``, ``model_path``, the attachment
        ``title`` / ``text``, and every allowlisted string extra — originates
        in operator YAML, a filesystem path, or an exception string, so all
        of it is treated as untrusted free text.

        Masking is idempotent, so ``reason`` (already masked + truncated
        upstream by :meth:`_mask_and_truncate_reason`) is unaffected by the
        second pass.
        """
        exempt = {"event", "status", "color"}

        def _walk(value: Any, key: Optional[str]) -> Any:
            if key in exempt:
                return value
            if isinstance(value, str):
                return cls._mask_free_text(value)
            if isinstance(value, dict):
                return {k: _walk(v, k) for k, v in value.items()}
            if isinstance(value, list):
                return [_walk(v, key) for v in value]
            return value

        return {k: _walk(v, k) for k, v in payload.items()}

    @staticmethod
    def _screen_extras(event: str, extra: Dict[str, Any]) -> Dict[str, Any]:
        """Filter ``**extra`` down to the allowlisted, transportable set.

        Two screens, in order:

        1. **Key allowlist** (``_ALLOWED_EXTRA_PAYLOAD_KEYS``) — an
           unregistered key is dropped, never transmitted.
        2. **Value type** — only JSON scalars survive; ``None`` is kept
           (it is a meaningful value for ``stopped_at``).

        Secret masking is *not* done here: it happens once for the whole
        assembled payload in :meth:`_mask_outbound_strings`, so surviving
        extras are masked on exactly the same terms as ``reason`` and the
        attachment prose.  Doing it per-argument is what let a raw
        ``stage_name`` leak through the Slack ``text`` field.

        *Why WARN-and-drop rather than raise.*
        ``error-handling.md`` forbids silent failures, and this is not one:
        the drop is logged at WARNING with the event name and the offending
        key, so an operator reading the run log sees it.  Raising was
        considered and rejected on the module's own contract — ``notify_*``
        is a best-effort side channel that "is never allowed to fail the
        training run" (``error-handling.md`` names webhook delivery in the
        best-effort carve-out).  An exception here would propagate out of
        ``notify_success`` and abort a completed, promoted run at its final
        step over a *notification-formatting* mistake, which is strictly
        worse than a missing Slack field.  Phase 14 already lived through
        the neighbouring version of this bug: unknown kwargs hit a fixed
        signature, the ``TypeError`` was swallowed by the orchestrator's
        best-effort wrapper, and pipeline webhooks silently never fired.
        The loudness the standard asks for is bought at CI time instead —
        ``tests/test_webhook.py`` drives every ``notify_*`` method and fails
        if any key it passes is missing from the allowlist, so the
        "contributor adds a field and forgets to register it" path is caught
        before release rather than in an operator's log.

        Being on the allowlist buys a key transport, not trust:
        ``stage_name`` and ``stopped_at`` are pipeline stage names read
        straight out of operator YAML, so they are config-derived text and
        still get masked downstream.  This complements, rather than
        bypasses, the URL redaction in :meth:`_mask` — that one protects the
        *destination*, this path protects the *body*.
        """
        screened: Dict[str, Any] = {}
        for key, value in extra.items():
            if key not in _ALLOWED_EXTRA_PAYLOAD_KEYS:
                logger.warning(
                    "Dropping unregistered webhook payload field %r for event '%s' — "
                    "not in the outbound allowlist (%s). Register it in "
                    "_ALLOWED_EXTRA_PAYLOAD_KEYS in forgelm/webhook.py if it is "
                    "intended to leave the process.",
                    key,
                    event,
                    ", ".join(sorted(_ALLOWED_EXTRA_PAYLOAD_KEYS)),
                )
                continue
            if value is not None and not isinstance(value, _ALLOWED_EXTRA_VALUE_TYPES):
                logger.warning(
                    "Dropping webhook payload field %r for event '%s' — value of type "
                    "%s is not a JSON scalar and would break payload serialization.",
                    key,
                    event,
                    type(value).__name__,
                )
                continue
            screened[key] = value
        return screened

    def notify_start(self, run_name: str) -> None:
        if self.config and self.config.notify_on_start:
            self._send(
                event="training.start",
                run_name=run_name,
                status="started",
                title=f"Training Started: {run_name}",
                text="The fine-tuning job has started.",
                color="#0052cc",
            )

    def notify_success(self, run_name: str, metrics: Dict[str, float]) -> None:
        if self.config and self.config.notify_on_success:
            metrics_str = "\n".join([f"• {k}: {v:.4f}" for k, v in metrics.items() if isinstance(v, (int, float))])
            self._send(
                event="training.success",
                run_name=run_name,
                status="succeeded",
                title=f"Training Succeeded: {run_name}",
                text=f"The job completed successfully.\n\nMetrics:\n{metrics_str}",
                color="#36a64f",
                metrics=metrics,
            )

    def notify_awaiting_approval(self, run_name: str, model_path: str) -> None:
        """Post an "awaiting human approval" notification (Art. 14 gate).

        Fired by :meth:`ForgeTrainer._handle_human_approval_gate` after the
        adapters have been saved to the staging directory. ``model_path`` is
        the on-disk staging location (``final_model.staging.<run_id>/``) so an
        approver can inspect the artefacts before running
        ``forgelm approve <run_id>``.

        Only the directory path is sent — the payload deliberately carries
        no model weights, tokenizer files, or compliance-bundle contents.
        Webhook receivers (Slack/Teams/Discord) regularly persist or echo
        message bodies, and we treat the approval signal as a notification,
        not an artefact transfer channel.
        """
        # Approval is only emitted on otherwise-successful runs, so it
        # piggy-backs on notify_on_success per the audit_event_catalog
        # webhook section. Operators who silenced success notifications do
        # not want approval pings either.
        if not (self.config and self.config.notify_on_success):
            return
        self._send(
            event="approval.required",
            run_name=run_name,
            status="awaiting_approval",
            title=f"Awaiting Human Approval: {run_name}",
            text=(
                "Training completed; the model is staged at "
                f"`{model_path}` and awaiting reviewer sign-off.\n"
                "Run `forgelm approve <run_id>` to promote, or "
                "`forgelm reject <run_id>` to discard."
            ),
            color="#f2c744",
            model_path=model_path,
        )

    def notify_failure(self, run_name: str, reason: str) -> None:
        """Post a training-failure notification.

        ``reason`` is whatever the trainer caught on the failure path —
        typically an exception ``str()`` that may carry filesystem paths,
        configured webhook URLs, or token-shaped strings from a stack
        trace. Run it through :func:`forgelm.data_audit.mask_secrets` so
        AWS / GitHub / Slack / OpenAI / Google / JWT / private-key blocks
        / Azure storage strings are redacted before the payload leaves
        the process.
        """
        if not (self.config and self.config.notify_on_failure):
            return
        masked_reason = self._mask_and_truncate_reason(reason)
        self._send(
            event="training.failure",
            run_name=run_name,
            status="failed",
            title=f"Training Failed: {run_name}",
            text=f"The training job encountered an error or evaluation failed.\n\nReason: {masked_reason}",
            color="#ff0000",
            reason=masked_reason,
        )

    @staticmethod
    def _mask_free_text(value: str) -> str:
        """Redact secret-shaped substrings from a single outbound string.

        Wraps :func:`forgelm.data_audit.mask_secrets` (AWS / GitHub / Slack /
        OpenAI / Google / JWT / private-key blocks / Azure storage strings).
        On ``ImportError`` the value is replaced wholesale rather than passed
        through: we cannot guarantee credentials were scrubbed, and shipping
        an un-scrubbed string to a third-party receiver is the worse option.
        """
        try:
            from .data_audit import mask_secrets

            return mask_secrets(value)
        except ImportError:
            # data_audit imports stay light enough that this should not
            # happen in practice.
            return "[REDACTED — secrets masker unavailable]"

    @classmethod
    def _mask_and_truncate_reason(cls, reason: str) -> str:
        """Mask secrets in *reason* and truncate to 2048 chars.

        Shared between :meth:`notify_failure` and :meth:`notify_reverted`
        so both lifecycle events get the same redaction guarantee. Falls
        back to a hard placeholder when ``data_audit`` cannot be imported
        because shipping an un-scrubbed stack trace is the worse option.
        """
        masked = cls._mask_free_text(reason)
        if isinstance(masked, str) and len(masked) > 2048:
            masked = masked[:2048] + "… (truncated)"
        return masked

    def notify_reverted(self, run_name: str, reason: str) -> None:
        """Post an auto-revert notification (lifecycle event ``training.reverted``).

        Distinct from :meth:`notify_failure` so dashboards can separate
        "training crashed" from "training succeeded but eval/safety/judge
        gates rejected the artifact and we deleted the adapters". The
        reason is masked + truncated identically to ``notify_failure`` so
        a leaked stack trace can't smuggle secrets via this path either.
        """
        if not (self.config and self.config.notify_on_failure):
            return
        masked_reason = self._mask_and_truncate_reason(reason)
        self._send(
            event="training.reverted",
            run_name=run_name,
            status="reverted",
            title=f"Training Reverted: {run_name}",
            text=(
                "Auto-revert fired. Generated artifacts were deleted because a "
                "post-training gate (evaluation, safety, judge, or benchmark) "
                f"rejected the run.\n\nReason: {masked_reason}"
            ),
            color="#ff9900",
            reason=masked_reason,
        )

    # ----------------------------------------------------------------------
    # Phase 14 — pipeline-level notifications
    # ----------------------------------------------------------------------
    #
    # The pipeline orchestrator drives multi-stage runs and emits its own
    # ``pipeline.*`` events alongside (not replacing) the existing per-stage
    # ``training.*`` events that each ``ForgeTrainer`` instance still fires.
    # Pre-existing Slack / Teams dashboards filtering on ``training.failure``
    # therefore keep working unchanged; pipeline-aware dashboards can
    # additionally subscribe to the new ``pipeline.*`` event vocabulary.

    def notify_pipeline_started(self, run_id: str, stage_count: int) -> None:
        """Post a "pipeline started" notification.

        Fires once per pipeline run, before any stage executes.  Operators
        running long chains use this signal to confirm the orchestrator
        accepted the config and started the first stage.
        """
        if not (self.config and self.config.notify_on_start):
            return
        self._send(
            event="pipeline.started",
            run_name=run_id,
            status="started",
            title=f"Pipeline Started: {run_id}",
            text=f"Multi-stage training pipeline began with {stage_count} stage(s).",
            color="#0052cc",
            stage_count=stage_count,
        )

    def notify_pipeline_completed(
        self,
        run_id: str,
        final_status: str,
        stopped_at: Optional[str],
    ) -> None:
        """Post a "pipeline completed" notification.

        Fires once per pipeline run, after the final stage transition
        (success, failure, or revert).  ``stopped_at`` names the failing
        stage when the chain halted; ``None`` on full success.

        Piggy-backs on ``notify_on_success`` when the pipeline finished
        cleanly and on ``notify_on_failure`` when it stopped early —
        matches the existing single-stage notification policy so
        operators don't see pipeline pings they explicitly silenced.
        """
        succeeded = final_status == "completed"
        if succeeded:
            if not (self.config and self.config.notify_on_success):
                return
            color = "#36a64f"
            title = f"Pipeline Succeeded: {run_id}"
            text = "All stages completed successfully."
        else:
            if not (self.config and self.config.notify_on_failure):
                return
            color = "#cc0000"
            title = f"Pipeline Stopped: {run_id}"
            text = f"Pipeline halted at stage {stopped_at!r} with final_status={final_status!r}."

        self._send(
            event="pipeline.completed",
            run_name=run_id,
            status=final_status,
            title=title,
            text=text,
            color=color,
            final_status=final_status,
            stopped_at=stopped_at,
        )

    def notify_pipeline_reverted(self, run_id: str, stage_name: str, reason: str) -> None:
        """Post a "pipeline stage auto-reverted" notification.

        Distinct from ``notify_pipeline_completed(final_status='stopped_at_stage')``:
        this fires *at the moment* a stage auto-reverts, before downstream
        stages are marked skipped.  Operators monitoring a long chain see
        the revert event in near-real-time rather than waiting for the
        final summary at the end of the run.

        The reason is masked + truncated identically to
        :meth:`notify_failure` so a leaked stack trace cannot smuggle
        secrets via this path either.
        """
        if not (self.config and self.config.notify_on_failure):
            return
        masked_reason = self._mask_and_truncate_reason(reason)
        self._send(
            event="pipeline.stage_reverted",
            run_name=run_id,
            status="reverted",
            title=f"Pipeline Stage Reverted: {run_id}",
            text=(
                f"Stage {stage_name!r} triggered auto-revert; downstream stages "
                f"will not run.\n\nReason: {masked_reason}"
            ),
            color="#ff9900",
            stage_name=stage_name,
            reason=masked_reason,
        )
