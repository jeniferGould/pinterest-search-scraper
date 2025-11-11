from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Union

from dateutil import parser as date_parser

logger = logging.getLogger("utils_time")

def _safe_to_datetime(
    value: Union[str, int, float, None]
) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            logger.debug("Failed to parse numeric timestamp %r", value)
            return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            dt = date_parser.parse(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            logger.debug("Failed to parse timestamp string %r", value)
            return None

    logger.debug("Unsupported timestamp type: %r", type(value))
    return None

def parse_pinterest_timestamp(
    raw: Union[str, int, float, None]
) -> Dict[str, Any]:
    """
    Convert a raw Pinterest timestamp into a standardized structure:

    {
        "formatted": "YYYY-MM-DD" or None,
        "initial": "<original string or None>"
    }
    """
    dt = _safe_to_datetime(raw)
    if dt is None:
        return {"formatted": None, "initial": raw}

    formatted = dt.strftime("%Y-%m-%d")
    return {"formatted": formatted, "initial": raw}