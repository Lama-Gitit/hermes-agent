"""
Helper for reading runtime secrets that may not survive the Hermes
gateway's os.environ strip.

Pattern: Hermes gateway overwrites os.environ on startup (see comment at
write_env.py:117 and the supabase-messages hook for the established
workaround). Tools that need credentials post-gateway-startup can't rely
on os.environ alone — they must also check a file the gateway hasn't
touched.

`write_env.py` bakes all allow-listed credentials into
`/root/.hermes/runtime_secrets.py` as a Python module of plain string
constants, written before the gateway takes over. This loader imports
that module on first use and exposes a `get_secret(name)` helper that
prefers os.environ when set, falls back to the baked module otherwise.

Usage:
    from tools.secrets_loader import get_secret
    api_key = get_secret("ALCHEMY_POLYGON_API_KEY") or "demo"
"""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SECRETS_PATH = Path("/root/.hermes/runtime_secrets.py")
_baked_module: Optional[object] = None
_load_attempted = False


def _load_baked():
    """Lazy-load the baked secrets module, or set sentinel if missing."""
    global _baked_module, _load_attempted
    if _load_attempted:
        return _baked_module
    _load_attempted = True
    # Defensively handle PermissionError / OSError — if we can't even stat
    # the file, treat it as missing rather than crashing the caller.
    try:
        if not _SECRETS_PATH.exists():
            logger.debug("[secrets_loader] %s does not exist; baked secrets unavailable", _SECRETS_PATH)
            _baked_module = None
            return None
    except (PermissionError, OSError) as e:
        logger.debug("[secrets_loader] cannot stat %s: %s — treating as missing", _SECRETS_PATH, e)
        _baked_module = None
        return None
    try:
        spec = importlib.util.spec_from_file_location("runtime_secrets", _SECRETS_PATH)
        if spec is None or spec.loader is None:
            logger.warning("[secrets_loader] could not create spec for %s", _SECRETS_PATH)
            _baked_module = None
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _baked_module = module
        logger.info("[secrets_loader] loaded baked secrets from %s", _SECRETS_PATH)
    except Exception as e:
        logger.error("[secrets_loader] failed to load %s: %s", _SECRETS_PATH, e)
        _baked_module = None
    return _baked_module


def get_secret(name: str, default: str = "") -> str:
    """
    Return a runtime secret. Tries os.environ first; if unset/empty, falls
    back to the baked secrets module. Returns `default` if neither has it.

    All returned values are .strip()-ed.
    """
    v = os.environ.get(name, "")
    if isinstance(v, str) and v.strip():
        return v.strip()
    baked = _load_baked()
    if baked is not None:
        v = getattr(baked, name, "") or ""
        if isinstance(v, str) and v.strip():
            return v.strip()
    return default


def is_set(name: str) -> bool:
    """Whether a secret is available via env or baked module."""
    return bool(get_secret(name))
