"""Deterministic SELECT-only SQL guard.

This is the hard safety boundary: no matter what the LLM generates, only read-only
SELECT statements may reach the database. It is deterministic code (not an LLM), so it
cannot be prompt-injected or "reasoned around".

Ported from the Al-Tasnim agentic chatbot's validator node.

Enable/disable with the ALTASNIM_SELECT_ONLY env var (default: enabled).
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("wren-ai-service")

# Statement keywords that must never appear in a query we execute.
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|"
    r"GRANT|REVOKE|BACKUP|RESTORE|SHUTDOWN|RECONFIGURE|INTO)\b",
    re.IGNORECASE,
)
# Stored procedure / extended stored procedure calls (xp_cmdshell and friends).
_PROC = re.compile(r"\b(sp_|xp_)\w+", re.IGNORECASE)
# SQL comments, stripped before analysis so they cannot hide a second statement.
_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


class SqlGuardError(Exception):
    """Raised when a statement is not a safe, read-only SELECT."""


def _enabled() -> bool:
    return os.getenv("ALTASNIM_SELECT_ONLY", "true").strip().lower() not in (
        "false",
        "0",
        "no",
    )


def _strip_comments(sql: str) -> str:
    return _COMMENT.sub(" ", sql)


def is_select_only(sql: str) -> tuple[bool, str]:
    """Return (ok, reason). `reason` is empty when the statement is allowed."""
    if not _enabled():
        return True, ""

    if not sql or not sql.strip():
        return False, "Empty query."

    clean = _strip_comments(sql).strip().rstrip(";").strip()

    # Reject statement stacking (`SELECT ...; DROP TABLE ...`).
    if ";" in clean:
        return False, "Multiple statements are not allowed - only a single SELECT."

    lowered = clean.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return False, "Only read-only queries are allowed (must start with SELECT or WITH)."

    if match := _FORBIDDEN.search(clean):
        return False, f"Forbidden keyword '{match.group(0)}' - only read-only SELECT is permitted."

    if _PROC.search(clean):
        return False, "Stored procedure calls (sp_/xp_) are not allowed."

    return True, ""


def assert_select_only(sql: str) -> None:
    """Raise SqlGuardError when `sql` is not a safe, read-only SELECT."""
    ok, reason = is_select_only(sql)
    if not ok:
        logger.warning("[altasnim-guard] blocked non-SELECT statement: %s", reason)
        raise SqlGuardError(reason)
