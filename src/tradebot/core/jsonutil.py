"""Strict-JSON helpers: no NaN/Infinity (JavaScript's JSON.parse rejects them), numpy -> Python."""

from __future__ import annotations

import json
import math
from datetime import datetime
from enum import Enum
from typing import Any

import numpy as np


def sanitize(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): sanitize(v) for k, v in o.items()}
    if isinstance(o, list | tuple | set):
        return [sanitize(v) for v in o]
    if isinstance(o, bool | np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, float | np.floating):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, np.ndarray):
        return sanitize(o.tolist())
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, type):
        return o.__name__
    if o is None or isinstance(o, str | int):
        return o
    if hasattr(o, "to_dict"):
        return sanitize(o.to_dict())
    return str(o)


def dumps(o: Any, indent: int | None = None) -> str:
    return json.dumps(sanitize(o), indent=indent, allow_nan=False)
