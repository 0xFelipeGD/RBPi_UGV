"""Tests for YAML config loading and deep merge."""

import os
import tempfile
import yaml
from config.config_loader import deep_merge, load_config


def test_deep_merge_simple():
    base = {"a": 1, "b": 2}
    override = {"b": 3, "c": 4}
    result = deep_merge(base, override)
    assert result == {"a": 1, "b": 3, "c": 4}


def test_deep_merge_nested():
    base = {"mqtt": {"host": "localhost", "port": 8883}}
    override = {"mqtt": {"host": "myserver"}}
    result = deep_merge(base, override)
    assert result["mqtt"]["host"] == "myserver"
    assert result["mqtt"]["port"] == 8883  # Preserved from base


def test_deep_merge_does_not_mutate():
    base = {"a": {"b": 1}}
    override = {"a": {"c": 2}}
    result = deep_merge(base, override)
    assert "c" not in base["a"]
    assert result["a"]["b"] == 1
    assert result["a"]["c"] == 2


def test_load_config_defaults_only():
    """load_config should work with just defaults (no user config.yaml)."""
    config = load_config()
    assert "mqtt" in config
    assert "drive" in config
    assert "safety" in config
    assert config["mqtt"]["port"] == 8883


def test_load_config_with_user_override():
    """User config.yaml should override defaults via deep merge."""
    # Create a temp user config
    config_dir = os.path.dirname(os.path.abspath(__file__))
    config_parent = os.path.join(config_dir, "..", "config")
    user_config_path = os.path.join(config_parent, "config.yaml")

    had_config = os.path.exists(user_config_path)
    original_content = None
    if had_config:
        with open(user_config_path, "r") as f:
            original_content = f.read()

    try:
        with open(user_config_path, "w") as f:
            yaml.dump({"mqtt": {"host": "test-server"}}, f)

        config = load_config()
        assert config["mqtt"]["host"] == "test-server"
        assert config["mqtt"]["port"] == 8883  # Default preserved
    finally:
        if had_config and original_content is not None:
            with open(user_config_path, "w") as f:
                f.write(original_content)
        elif not had_config and os.path.exists(user_config_path):
            os.remove(user_config_path)
