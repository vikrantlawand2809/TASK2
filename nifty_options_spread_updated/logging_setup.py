"""Logging with secret redaction."""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterable

_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_TOKEN_KV = re.compile(r"((?:access_token|api_key|api_secret|client_secret)\s*[=:]\s*)[^\s,&'\"]+", re.IGNORECASE)


class SecretRedactingFilter(logging.Filter):
    """Masks bearer tokens, token=... pairs and any known secret values."""

    def __init__(self, secrets: Iterable[str] = ()):
        super().__init__()
        self._secrets = tuple(s for s in secrets if s and len(s) >= 6)

    def redact(self, text: str) -> str:
        text = _BEARER.sub(r"\1***", text)
        text = _TOKEN_KV.sub(r"\1***", text)
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = ()
        return True


def setup_logging(logs_dir: Path, level: int = logging.INFO, secrets: Iterable[str] = (),
                  console: bool = True) -> logging.Logger:
    """Configure the root 'nifty_spread' logger (idempotent)."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("nifty_spread")
    logger.setLevel(level)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    redactor = SecretRedactingFilter(secrets)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(logs_dir / "shadow_run.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    ]
    if console:
        handlers.append(logging.StreamHandler())
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(redactor)
        logger.addHandler(h)
    return logger
