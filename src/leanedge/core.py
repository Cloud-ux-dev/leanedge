"""Small shared primitives. All accounting uses Decimal; clocks are UTC."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

D = Decimal
ZERO = D(0)


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value=None):
    value = value or utcnow()
    if value.tzinfo is None:
        raise ValueError("A UTC-aware time is required")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def instant(value):
    if not isinstance(value, str):
        raise ValueError("A UTC-aware time is required")
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("A UTC-aware time is required")
    return t.astimezone(timezone.utc)


def number(value, setting="numeric field", *, positive=False):
    try:
        if isinstance(value, bool):
            raise ValueError()
        n = D(str(value))
        if not n.is_finite() or n < 0 or (positive and n <= 0):
            raise ValueError()
        return n
    except (ValueError, InvalidOperation, TypeError):
        raise ValueError(f"{setting}: invalid nonnegative finite number") from None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False, default=lambda x: str(x) if isinstance(x, D) else stamp(x))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def redact(value):
    """Store numeric API fields, but redact credentials and arbitrary URLs recursively."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if re.search(r"secret|password|authorization|api.?key|credential|url|website|description|name|symbol", k, re.I)
                    else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and re.search(r"https?://|bearer\s|sk-[a-z0-9]", value, re.I):
        return "[REDACTED]"
    return value
