#!/usr/bin/env python3
"""Shared logging setup for the motion capture scripts."""

import logging
import logging.handlers
from pathlib import Path


LOG_DIR = Path("/home/pi/BEAMNode_Prototype2/logs")
LOG_PATH = LOG_DIR / "motion.log"


def setup_motion_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_PATH,
            maxBytes=1_000_000,
            backupCount=5,
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except Exception as exc:
        logger.warning("File logging disabled for %s: %s", LOG_PATH, exc)

    return logger
