from __future__ import annotations

from datetime import datetime, timezone


ISO_UTC_SUFFIX = "Z"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso8601(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", ISO_UTC_SUFFIX)


def parse_iso8601(ts: str) -> datetime:
    if ts.endswith(ISO_UTC_SUFFIX):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).astimezone(timezone.utc)
