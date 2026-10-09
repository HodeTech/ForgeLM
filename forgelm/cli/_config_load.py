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
        return load_config(config_path)
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


def _apply_offline_flag(config, offline_arg: bool) -> None:
    if offline_arg:
        config.model.offline = True
    if config.model.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_DATASETS_OFFLINE"] = "1"
        logger.info("Offline mode enabled. All HF Hub network calls are disabled.")
