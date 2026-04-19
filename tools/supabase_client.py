"""
Shared Supabase client for Hermes TCG tools and hooks.

Initializes a singleton client from SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
environment variables.  All Supabase-touching code should import `get_client()`
from here rather than creating its own client.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_client = None


def get_client():
    """Return a cached Supabase client, or None if env vars are missing."""
    global _client
    if _client is not None:
        return _client

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if not url or not key:
        logger.warning(
            "[supabase] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set — "
            "Supabase tools will be unavailable"
        )
        return None

    try:
        from supabase import create_client
        _client = create_client(url, key)
        logger.info("[supabase] Client initialized for %s", url)
        return _client
    except Exception as e:
        logger.error("[supabase] Failed to create client: %s", e)
        return None


def is_available() -> bool:
    """Check whether Supabase credentials are configured."""
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    return bool(url and key)
