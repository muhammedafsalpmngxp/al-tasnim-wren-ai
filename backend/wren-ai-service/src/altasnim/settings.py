"""Feature toggles for the Al-Tasnim layer.

Kept in this package on purpose: `src.altasnim` imports nothing from `src.pipelines` or
`src.web`, so any module can read a toggle without creating an import cycle.

(`src.web.v1.services.ask` needs the verifier toggle, while
`src.pipelines.generation.utils.sql` imports from `src.web.v1.services.ask` - importing the
toggle from the pipeline module directly would close that loop.)
"""

from __future__ import annotations

import os

_FALSEY = ("false", "0", "no", "off")


def _flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSEY


def select_only_enabled() -> bool:
    """Deterministic read-only SQL guard (ALTASNIM_SELECT_ONLY)."""
    return _flag("ALTASNIM_SELECT_ONLY")


def domain_rules_enabled() -> bool:
    """Domain/accuracy rule injection (ALTASNIM_DOMAIN_RULES)."""
    return _flag("ALTASNIM_DOMAIN_RULES")


def verifier_enabled() -> bool:
    """Independent SQL verifier; costs one extra LLM call (ALTASNIM_VERIFIER)."""
    return _flag("ALTASNIM_VERIFIER")
