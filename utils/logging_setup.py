"""Structured logging configuration with rotating file handler."""

import logging
import sys
import os
from logging.handlers import RotatingFileHandler


def setup_logging(config: dict) -> None:
    """Configure logging for the UGV daemon.

    Supports console output and rotating file output. Silences noisy
    third-party libraries (paho MQTT).
    """
    log_cfg = config.get("logging", {})
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger("ugv")
    root.setLevel(level)

    if log_cfg.get("console", True):
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        root.addHandler(console)

    log_file = log_cfg.get("file", "")
    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            max_bytes = log_cfg.get("max_bytes", 5_242_880)
            backup_count = log_cfg.get("backup_count", 3)
            file_handler = RotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=backup_count,
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except PermissionError:
            root.warning(f"Cannot write to log file {log_file} — console only")

    # Silence noisy libraries
    logging.getLogger("paho").setLevel(logging.WARNING)
