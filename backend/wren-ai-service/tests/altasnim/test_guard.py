"""Tests for the Al-Tasnim read-only SQL guard.

The guard is the hard safety boundary, so every destructive shape must be rejected.
"""

import pytest

from src.altasnim.guard import SqlGuardError, assert_select_only, is_select_only

ALLOWED = [
    "SELECT * FROM core.Well",
    "select well_name from core.Well where is_active = 1",
    "SELECT TOP (10) well_name FROM core.Well ORDER BY well_name",
    "WITH ranked AS (SELECT well_key FROM core.ProgressSnapshot) SELECT * FROM ranked",
    "SELECT COUNT(*) FROM core.Well;",  # single trailing semicolon is fine
    "-- a comment\nSELECT 1",
]

BLOCKED = [
    "",
    "   ",
    "DELETE FROM core.Well",
    "UPDATE core.Well SET is_active = 0",
    "INSERT INTO core.Well (well_name) VALUES ('x')",
    "DROP TABLE core.Well",
    "TRUNCATE TABLE core.Well",
    "ALTER TABLE core.Well ADD c INT",
    "CREATE TABLE t (id INT)",
    "MERGE INTO core.Well USING x ON 1=1 WHEN MATCHED THEN DELETE",
    "EXEC sp_who",
    "EXECUTE xp_cmdshell 'dir'",
    "GRANT SELECT ON core.Well TO public",
    "SELECT * FROM core.Well; DROP TABLE core.Well",  # statement stacking
    "SELECT * INTO backup FROM core.Well",  # SELECT ... INTO writes a table
    "SELECT 1 /* hide */ ; DELETE FROM core.Well",  # comment-hidden second statement
    "sp_configure 'show advanced options', 1",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allows_read_only_selects(sql):
    ok, reason = is_select_only(sql)
    assert ok, f"should have been allowed but got: {reason}"
    assert_select_only(sql)  # must not raise


@pytest.mark.parametrize("sql", BLOCKED)
def test_blocks_everything_else(sql):
    ok, reason = is_select_only(sql)
    assert not ok, f"should have been blocked: {sql!r}"
    assert reason, "a rejection must explain itself"
    with pytest.raises(SqlGuardError):
        assert_select_only(sql)


def test_guard_can_be_disabled_via_env(monkeypatch):
    monkeypatch.setenv("ALTASNIM_SELECT_ONLY", "false")
    ok, _ = is_select_only("DELETE FROM core.Well")
    assert ok, "guard should be a no-op when explicitly disabled"


def test_guard_enabled_by_default(monkeypatch):
    monkeypatch.delenv("ALTASNIM_SELECT_ONLY", raising=False)
    ok, _ = is_select_only("DELETE FROM core.Well")
    assert not ok, "guard must default to enabled"
