"""JSON logs on stdout. Vector picks them up from docker.sock automatically and ships to Loki.

REDACTION IS MANDATORY: stdout reaches a second system, so a token in a log is a leak into
Loki and into Slack. Anything that looks like a credential is filtered out BEFORE it leaves
the process.
"""
import json
import re
import sys

from loguru import logger

from app.config import settings

_SECRETS = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]+)"
    r"|(Bearer\s+[A-Za-z0-9._\-]{12,})"
    r"|(\"accessToken\"\s*:\s*\"[^\"]+\")"
    r"|(\"refreshToken\"\s*:\s*\"[^\"]+\")",
    re.IGNORECASE,
)


def _redact(text: str) -> str:
    return _SECRETS.sub("[ZREDAGOWANO]", text)


def _sink(message) -> None:
    r = message.record
    payload = {
        "ts": r["time"].isoformat(),
        "level": r["level"].name,
        "logger": r["name"],
        "msg": _redact(r["message"]),
    }
    if r["exception"] is not None:
        payload["exc"] = _redact(str(r["exception"]))
    extra = {k: v for k, v in (r["extra"] or {}).items()}
    if extra:
        payload["extra"] = json.loads(_redact(json.dumps(extra, default=str, ensure_ascii=False)))
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def configure() -> None:
    logger.remove()
    logger.add(_sink, level=settings.log_level.upper(), enqueue=False, backtrace=False)
