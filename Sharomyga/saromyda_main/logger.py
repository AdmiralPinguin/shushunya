import logging
from . import config as cfg

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level_name = str(getattr(cfg, "LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    fmt = getattr(cfg, "LOG_FORMAT", "%(asctime)s %(levelname).1s %(name)s: %(message)s")
    datefmt = getattr(cfg, "LOG_DATEFMT", "%m-%d %H:%M:%S")

    formatter = logging.Formatter(fmt=fmt, datefmt=datefmt)
    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(formatter)

    logger.addHandler(ch)
    logger.propagate = False
    return logger
