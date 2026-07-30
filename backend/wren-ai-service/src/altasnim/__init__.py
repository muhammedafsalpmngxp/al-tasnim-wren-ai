"""Al-Tasnim customisations for Wren AI.

A self-contained package that layers the accuracy + safety guarantees of the custom
Al-Tasnim agentic chatbot on top of Wren AI, while keeping Wren AI's chart generation,
semantic modelling and UI untouched.

Contents
--------
guard.py         Deterministic SELECT-only SQL guard (defence in depth before execution).
domain_rules.py  Domain/business prompt rules injected into SQL generation and answering.

Everything here is additive: if `ALTASNIM_ENABLED=false` the behaviour falls back to
stock Wren AI.
"""

from src.altasnim.domain_rules import (
    answer_integrity_rules,
    domain_instructions,
    sql_generation_rules,
)
from src.altasnim.guard import (
    SqlGuardError,
    assert_select_only,
    is_select_only,
)

__all__ = [
    "SqlGuardError",
    "assert_select_only",
    "is_select_only",
    "answer_integrity_rules",
    "domain_instructions",
    "sql_generation_rules",
]
