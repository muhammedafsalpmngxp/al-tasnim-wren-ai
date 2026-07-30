"""Al-Tasnim domain + accuracy rules injected into Wren AI's prompts.

These are the rules that made the standalone Al-Tasnim agentic chatbot accurate on
complex, multi-table questions. They are injected as high-priority instructions into
every SQL generation call, and as integrity rules into the answering step.

Design notes
------------
* Rules are GENERAL (patterns, not hardcoded queries) so they apply to every question.
* They are grouped so the model can follow them as a checklist.
* Anything domain-specific and still unconfirmed is deliberately left out - the model is
  told to ask rather than guess.

Toggle with ALTASNIM_DOMAIN_RULES (default: enabled).
"""

from __future__ import annotations

import os

# --------------------------------------------------------------------------------------
# 1. Intent fidelity - answer exactly what was asked, nothing more
# --------------------------------------------------------------------------------------
_INTENT_RULES = """
[AL-TASNIM] INTENT FIDELITY - answer exactly what was asked:
1. First restate the user's intent internally: the entity, the exact fields requested, any
   filters they actually stated, and the result size (all / top N / sample / count). Then
   build SQL for EXACTLY that - nothing more.
2. NEVER add a filter the user did not ask for and that is not logically required. Do not
   auto-add is_active, is_current, latest-record, date, project or status filters, and do
   not add IS NOT NULL / <> '' on any table unless the user asked. "list wells" means all
   wells; only "active wells" adds an active filter, "inactive wells" adds the inverse.
3. Do not add DISTINCT unless the question genuinely requires unique rows.
4. Return only the fields the user asked about. Do not add extra dimensions or attributes
   they did not request, and do not include internal/audit columns (created_at,
   modified_at, source_system_id, surrogate *_key ids) unless explicitly asked.
5. Respect the requested result size: "all" -> no TOP; "top N"/"N" -> exactly N with a
   matching ORDER BY; "sample"/"a few" -> a small sample; "how many" -> a COUNT.
"""

# --------------------------------------------------------------------------------------
# 2. Schema, joins and mapping - the biggest source of complex-query errors
# --------------------------------------------------------------------------------------
_SCHEMA_RULES = """
[AL-TASNIM] SCHEMA, JOINS AND MAPPINGS:
6. Use ONLY tables and columns that exist in the provided schema. Never invent a name or a
   relationship, and never guess a column that "sounds right".
7. Follow the ACTUAL defined relationships when joining. When two similarly named objects
   exist (for example a lookup table versus a master table), pick the one the real
   relationship points to - do not choose by name similarity.
8. Every JOIN must earn its place: it must contribute a selected field, a filter or a
   calculation. Do not add joins that are unused in the final result.
9. Do not use an arbitrary MAX()/MIN() on a descriptive field just to satisfy GROUP BY (for
   example MAX(name) to represent a group). Return proper group-level metrics, or identify
   a specific row with an explicit ranking (ROW_NUMBER/RANK).
10. Never let one arbitrary row stand in for a whole group.
"""

# --------------------------------------------------------------------------------------
# 3. Correctness of values, time and units
# --------------------------------------------------------------------------------------
_CORRECTNESS_RULES = """
[AL-TASNIM] CORRECTNESS OF VALUES, TIME AND UNITS:
11. For "latest", "current" or "most recent", select the latest record per entity using the
    correct date/sequence column (ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ... DESC) or
    MAX()). Do NOT average across history when the user asked for the current value.
12. Treat snapshot/history tables as time series: pick the newest row per entity for current
    state, and use the full series only for trends.
13. NEVER treat NULL or missing data as zero. Distinguish zero vs missing vs unavailable vs
    not-applicable. A NULL date means "unknown", not 0 - never compute a delay, average or
    count as if NULL were 0.
14. Validate the meaning and scale of numeric fields before calculating. Progress-style
    values stored as decimals between 0 and 1 are fractions (0.27 = 27%) - multiply by 100
    for a percentage; do not mix fraction and percentage columns in one calculation.
15. Integer date keys in YYYYMMDD form must be converted to a real date before date maths,
    and only when the value is a valid, positive key.
16. Do totals, averages, counts and rankings in SQL (SUM/AVG/COUNT/MIN/MAX with GROUP BY,
    or window functions) so the database computes them exactly - never by eyeballing rows.
"""

# --------------------------------------------------------------------------------------
# 4. Dialect safety - the observed DATE_TRUNC class of failures
# --------------------------------------------------------------------------------------
_DIALECT_RULES = """
[AL-TASNIM] DIALECT SAFETY (target database is Microsoft SQL Server):
17. Only use functions that exist for the target database. If a SQL FUNCTIONS list is
    provided in the input, you MUST choose from it.
18. Never use PostgreSQL/ANSI-only constructs that SQL Server does not implement, in
    particular DATE_TRUNC, EXTRACT, ILIKE, LIMIT/OFFSET and :: casts.
19. For date grouping and date maths use the SQL Server family of functions - YEAR(),
    MONTH(), DAY(), DATEPART, DATENAME, DATEADD, DATEDIFF, CONVERT/FORMAT. Row limiting
    uses TOP (n), not LIMIT.
20. Prefer clear, traceable SQL over clever SQL. Use CTEs for multi-step logic so each step
    can be checked.
"""

# --------------------------------------------------------------------------------------
# 5. Safety - read-only, always
# --------------------------------------------------------------------------------------
_SAFETY_RULES = """
[AL-TASNIM] READ-ONLY SAFETY:
21. Generate a SINGLE read-only SELECT statement (a WITH ... SELECT is fine). Never generate
    INSERT, UPDATE, DELETE, MERGE, DROP, ALTER, CREATE, TRUNCATE, EXEC, or multiple
    statements. Anything else is rejected before execution.
22. Never query internal application, configuration, credential, telemetry or staging
    tables, and never select any password/secret/token column, even if asked.
"""

# --------------------------------------------------------------------------------------
# 6. Self-check before returning SQL
# --------------------------------------------------------------------------------------
_SELF_CHECK_RULES = """
[AL-TASNIM] SELF-CHECK BEFORE RETURNING THE SQL:
23. Verify: does this answer the user's exact question? Are the tables and columns real and
    joined through their real relationships? Is latest-vs-historical handled correctly? Are
    units and scale right? Are there any filters, joins or DISTINCT that were not requested?
24. If the question is genuinely ambiguous or depends on a business definition you have not
    been given, do not invent one - answer the most reasonable literal reading and state the
    assumption, or ask for clarification.
"""

# --------------------------------------------------------------------------------------
# Answer integrity rules (used when turning SQL results into a written answer)
# --------------------------------------------------------------------------------------
_ANSWER_RULES = """
[AL-TASNIM] ANSWER INTEGRITY:
1. Every number, ranking, comparison and conclusion must come from the returned data or a
   transparent calculation on it. Never fabricate data, totals, averages, thresholds or
   conclusions. If the data is insufficient, say plainly what is missing.
2. Never treat NULL/missing as zero - distinguish zero, missing, unavailable and
   not-applicable.
3. Do not claim a composite score or index unless one was actually computed. If the result
   is merely sorted by several columns, describe it as a ranking by those measures.
4. For risk/at-risk/attention style answers, state the criteria used, derived from the data.
   Do not invent thresholds or weights.
5. Do not make unsupported causal claims. Separate facts from interpretation, assumptions
   and recommendations.
6. Label forecasts and estimates as such, and state what they are based on.
7. Validate units and scale before reporting a figure (fractions vs percentages).
8. Write for business users: clear, plain, professional language. Do not expose internal
   table names, column names, schema names or SQL in the answer, and do not report a raw
   flag such as is_active = 0 - say "inactive" instead.
"""


def _enabled() -> bool:
    return os.getenv("ALTASNIM_DOMAIN_RULES", "true").strip().lower() not in (
        "false",
        "0",
        "no",
    )


def sql_generation_rules() -> list[str]:
    """High-priority rules injected into every SQL generation prompt."""
    if not _enabled():
        return []
    return [
        _INTENT_RULES.strip(),
        _SCHEMA_RULES.strip(),
        _CORRECTNESS_RULES.strip(),
        _DIALECT_RULES.strip(),
        _SAFETY_RULES.strip(),
        _SELF_CHECK_RULES.strip(),
    ]


def answer_integrity_rules() -> str:
    """Rules appended to the SQL-to-answer system prompt."""
    if not _enabled():
        return ""
    return _ANSWER_RULES.strip()


def domain_instructions() -> list[str]:
    """Alias kept for readability at call sites."""
    return sql_generation_rules()
