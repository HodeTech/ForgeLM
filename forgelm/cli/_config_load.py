"""Config loading + offline-flag application for training mode."""

from __future__ import annotations

import os
import sys

import yaml
from pydantic import ValidationError

from .._strict_json import dumps_strict
from ..config import ConfigError, load_config
from ._exit_codes import EXIT_CONFIG_ERROR
from ._logging import logger


def _warn_reserved_retention_enforce(config) -> None:
    """``retention.enforce`` is validated and offered by both wizards, but no command acts on it.

    A config that sets ``warn_on_excess`` or ``block_on_excess`` is asking for a gate that does not exist; say so,
    rather than accept it silently.  (This lives here, not in the schema, to keep ``config.py`` within its
    deferred-split line budget.)
    """
    mode = getattr(getattr(config, "retention", None), "enforce", "log_only")
    if mode != "log_only":
        logger.warning(
            "retention.enforce=%r is accepted but no command acts on it yet: retention horizons are only reported "
            "by `forgelm purge --check-policy`, and the trainer is not warned or blocked.",
            mode,
        )


def _reject_unsloth_unsupported_overrides(config) -> None:
    """Fail at config load (exit 1, also under ``--dry-run``) when the unsloth backend cannot apply an override."""
    from ..model import _reject_unsloth_unsupported_overrides as reject

    reject(config)


def _load_config_or_exit(config_path: str, json_output: bool):
    """Load config and translate exceptions into the right exit code.

    Catches concrete classes so Pydantic / YAML errors keep their
    line-and-column information. The previous version's bare
    ``except Exception`` swallowed the structured detail Pydantic
    returns and replaced it with ``Unexpected error:`` — making
    "trainer_type misspelled" indistinguishable from "YAML truncated".
    """
    try:
        logger.info("Loading configuration from %s...", config_path)
        config = load_config(config_path)
        _warn_reserved_retention_enforce(config)
        _reject_unsloth_unsupported_overrides(config)
        return config
    except FileNotFoundError as e:
        msg = f"Config file not found: {e}"
    except ConfigError as e:
        # Already a translated error from forgelm.config.load_config —
        # preserve message verbatim.
        msg = str(e)
    except yaml.YAMLError as e:
        msg = f"Invalid YAML syntax in {config_path}: {e}"
    except ValidationError as e:
        # Pydantic ValidationError's str() lists field path + reason
        # for each error — keep that structured detail.
        msg = f"Configuration validation failed:\n{e}"
    except OSError as e:
        msg = f"Could not read config file {config_path}: {e}"
    if json_output:
        print(dumps_strict({"success": False, "error": msg}))
    else:
        logger.error("Configuration error: %s", msg)
    sys.exit(EXIT_CONFIG_ERROR)


def _disable_hub_telemetry() -> None:
    """Keep the ``forgelm`` CLI from sending Hugging Face's anonymous usage ping.

    A training run makes ``huggingface_hub`` send a small usage ping to huggingface.co: TRL's trainers call
    ``send_telemetry`` when they are built, and ``huggingface_hub`` skips it only when ``HF_HUB_DISABLE_TELEMETRY`` is
    true or offline mode is on.  ForgeLM's privacy page says the tool sends no usage data anywhere, so the CLI sets
    the variable by default.  ``setdefault`` leaves an operator's own value alone (``HF_HUB_DISABLE_TELEMETRY=0``
    opts back in).  It must run before ``huggingface_hub`` is imported, which reads it once at import; importing
    ``forgelm.cli`` does not import it, and ``main()`` calls this first.  Using ForgeLM as a library does not go
    through ``main()``, so a library user sets the variable themselves.
    """
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def _apply_offline_flag(config, offline_arg: bool) -> None:
    if offline_arg:
        config.model.offline = True
    if config.model.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_DATASETS_OFFLINE"] = "1"
        logger.info("Offline mode enabled. All HF Hub network calls are disabled.")


def _warn_inert_fsdp_options(config) -> None:
    """Say so when an FSDP knob that transformers' FSDP2 default ignores is set away from its default.

    ``fsdp_auto_wrap: false`` and ``fsdp_backward_prefetch: backward_post`` are still accepted, but under
    transformers 5.10+ (FSDP2) the transformer-layer wrap policy is always applied and no backward prefetch is
    passed on, so the run behaves as if the defaults had been kept.  (This lives here, not in the schema, to keep
    ``config.py`` within its deferred-split line budget.)
    """
    dist = getattr(config, "distributed", None)
    if getattr(dist, "strategy", None) != "fsdp":
        return
    if not dist.fsdp_auto_wrap:
        logger.warning(
            "distributed.fsdp_auto_wrap=false has no effect: transformers 5.10+ runs FSDP2, which always wraps "
            "transformer layers."
        )
    if dist.fsdp_backward_prefetch != "backward_pre":
        logger.warning(
            "distributed.fsdp_backward_prefetch=%r has no effect: transformers 5.10+ runs FSDP2, which does not take "
            "a backward-prefetch policy.",
            dist.fsdp_backward_prefetch,
        )
