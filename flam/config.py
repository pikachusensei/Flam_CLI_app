import json
import os

CONFIG_PATH = "config.json"

DEFAULT_CONFIG = {
    "max_retries": 3,
    "base_backoff": 2,
    "default_timeout": 30,
    "poll_interval": 1,
    "priority_default": 0,
}


def load_config():
    """Load configuration file, create with defaults if missing."""
    if not os.path.exists(CONFIG_PATH):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

    with open(CONFIG_PATH, "r") as f:
        try:
            cfg = json.load(f)
        except:
            cfg = {}

    # Fill missing keys with defaults
    for key, val in DEFAULT_CONFIG.items():
        cfg.setdefault(key, val)

    return cfg


def save_config(cfg: dict):
    """Save configuration dictionary to disk."""
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=4)
