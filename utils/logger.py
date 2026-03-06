"""
Nive'secureAppLock - Logging utility.
Configures file + console logging for authentication events and process watcher actions.
"""

import logging
import os
from datetime import datetime


def setup_logger(name: str = "NiveSecureAppLock") -> logging.Logger:
    """Configure and return the application logger."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    log_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
    )
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, f"nivesecureapplock_{datetime.now():%Y%m%d}.log")

    # File handler - DEBUG level
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(module)-18s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    # Console handler - INFO level
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(levelname)-8s | %(message)s"))

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger
