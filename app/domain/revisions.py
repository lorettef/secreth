"""Compact optimistic revision keys for entity-bound Telegram actions."""

import hashlib
import json


def revision(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
