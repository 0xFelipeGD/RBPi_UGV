"""YAML configuration loader with deep merge support."""

import os
import copy
import logging

import yaml

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(CONFIG_DIR, "default_config.yaml")
USER_CONFIG_CANDIDATES = [
    os.path.join(CONFIG_DIR, "config.yaml"),
    os.path.join(CONFIG_DIR, "..", "config.yaml"),
]


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override values win."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config() -> dict:
    """Load default config, then overlay user config if present.

    Users only need to specify the keys they want to override.
    Missing config.yaml is fine — defaults are used.
    """
    with open(DEFAULT_CONFIG, "r") as f:
        config = yaml.safe_load(f)

    for candidate in USER_CONFIG_CANDIDATES:
        if os.path.isfile(candidate):
            with open(candidate, "r") as f:
                user_cfg = yaml.safe_load(f) or {}
            config = deep_merge(config, user_cfg)
            logging.getLogger("ugv.config").info(f"Loaded user config: {candidate}")
            break

    return config
