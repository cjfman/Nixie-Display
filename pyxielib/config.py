"""Master application config: load a YAML file and apply it under the CLI.

Precedence is CLI argument > config file > hardcoded default (see ``resolve``).
Polling periods are additionally clamped to safe bounds so a typo in the config
can't stall or thrash the scheduler/assembler threads.

The loader intentionally just returns a plain dict, so adding a future section
(e.g. a configurable cron ``schedule:``) is a localized change in the caller.
"""

import logging
from typing import Any, Dict

import yaml

from pyxielib.pyxieutil import PyxieError

logger = logging.getLogger(__name__)


class ConfigError(PyxieError):
    pass


## Polling-period safeguards: key -> (default, lo, hi) in seconds. Config values
## are clamped to [lo, hi]; scheduler_active_period is also capped at the resolved
## scheduler_period (see polling_periods).
POLLING_BOUNDS = {
    'assembler_poll_interval': (0.001, 0.0005, 0.05),
    'scheduler_period':        (0.1,   0.01,   5.0),
    'scheduler_active_period': (0.02,  0.005,  5.0),
}


def load_config(path) -> Dict[str, Any]:
    """Load a YAML config file into a dict. Empty dict when path is None."""
    if path is None:
        return {}

    try:
        with open(path) as f:
            data = yaml.safe_load(f)
    except OSError as e:
        raise ConfigError(f"Could not read config file '{path}': {e}")
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML in config file '{path}': {e}")

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file '{path}' must contain a mapping at the top level")

    return data


def resolve(cli_value, cfg: Dict[str, Any], key, default=None):
    """First non-None of CLI value, config[key], default — the precedence rule.

    A config value of ``False`` counts as set, so a boolean config option is
    honored unless the CLI explicitly overrides it.
    """
    if cli_value is not None:
        return cli_value
    if cfg.get(key) is not None:
        return cfg[key]

    return default


def clamp(value, lo, hi):
    """Constrain value to the inclusive range [lo, hi]."""
    return max(lo, min(hi, value))


def polling_periods(cfg: Dict[str, Any]) -> Dict[str, float]:
    """Resolve and clamp the polling periods from the 'polling' config section.

    'scheduler_active_period' is additionally capped at the resolved
    'scheduler_period' so the active (menu) poll is never slower than idle.
    """
    section = cfg.get('polling') or {}
    periods = {}
    for key, (default, lo, hi) in POLLING_BOUNDS.items():
        value = section.get(key)
        periods[key] = clamp(float(value), lo, hi) if value is not None else default

    periods['scheduler_active_period'] = min(
        periods['scheduler_active_period'], periods['scheduler_period'])
    return periods
