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

from src.altasnim.settings import domain_rules_enabled

# --------------------------------------------------------------------------------------
# 0. Al-Tasnim business definitions - the domain knowledge the model cannot infer
#
# NOTE ON NAMING: this deployment's models are named with underscores (core_Well,
# ref_ActivityMaster, ...), so all references below use that form.
# NOTE ON FUNCTIONS: these rules state business FACTS, not implementations. Express them
# using only functions from the SQL FUNCTIONS list you were given.
# --------------------------------------------------------------------------------------
_BUSINESS_RULES = """
[AL-TASNIM] BUSINESS DEFINITIONS (authoritative - these override any guess):
- DOMAIN: an oil & gas drilling project database. The main entity is a WELL (core_Well).
- COMPLETED WELL: core_WellDeliveryEvent.Eng_Completion_Date IS NOT NULL. This column is the
  business term "Hook Up Completion". NULL means NOT completed.
- LIVE / ACTIVE WELL: core_Well.is_active = 1 AND core_Well.is_current = 1. Apply this ONLY
  when the user asks about live/active/current wells - never by default.
- RIG-ON: core_WellDeliveryEvent.Rig_On_Expected_Date is the target/expected date;
  Rig_On_Date is the ACTUAL date. A well MISSED its rig-on when the expected date is in the
  past and the actual date is still NULL.
- FLAF-READY: core_WellDocument.Flaf_Date IS NOT NULL (a well is FLAF-ready once Flaf_Date is
  set/passed). FLAF = Field Layout Approval Form.
- WBS TERMINOLOGY: "WBS" and "activity group" mean the SAME thing.
  * "WBS" / "list WBS" / "activity groups" -> the NAMES in ref_ActivityMaster.activity_group.
    This is the DEFAULT meaning of WBS.
  * "WBS code" / "WBS codes" -> core_ScheduleTask.wbs_code (hierarchical codes).
  * WBS-branch PROGRESS -> the wide columns of core_ProgressDetail.
  If the user just says "WBS", use activity_group; use wbs_code only when they say "code".
- LATEST STATUS: the *Snapshot tables are time series keyed by date_key. For
  "current"/"latest" status take the newest row per well (or per task) - never an average
  across history.
- DATE KEYS: *_date_key columns are INTEGERS in YYYYMMDD form (e.g. 20260607 = 2026-06-07),
  not real dates - convert before any date maths, and only when the value is a positive key
  (0 or NULL means unknown). ref_Date is EMPTY - do NOT join it to resolve dates. Columns
  already typed as date/timestamp (e.g. in core_WellDeliveryEvent, core_ScheduleTaskSnapshot)
  hold real dates - use them directly.
- PROGRESS SCALE: progress values are 0-1 FRACTIONS (0.2700 = 27%); multiply by 100 for a
  percentage. Prefer core_ProgressSnapshot.overall_progress
  (overall_progress_percentage is often 0/empty). core_ScheduleTaskSnapshot.progress and the
  core_ProgressDetail value columns are also 0-1 (1.0000 = complete).
- WBS BRANCH PROGRESS lives in the wide value columns of core_ProgressDetail, each 0-1. Link
  it to a well through core_WellScope (well_scope_key), because
  core_ProgressDetail.progress_snapshot_id may be NULL.
- WELL <-> PROJECT: the key is on the WELL side - core_Well.project_key points at
  core_Project.project_key. core_Project does NOT contain well_key.
- DATA STATE: the database is still being loaded. Some tables may be EMPTY and many rows
  currently have is_active = 0. If a query returns no rows it may be incomplete data rather
  than a wrong query - never state "there are none" as a certainty.
- SCOPE: only the drilling domain (core_*) and its lookups (ref_*) are in scope. Never query
  application, configuration, telemetry, staging or ml_* tables, and never select any
  password/secret/token column.
NOT YET DEFINED - if a question depends on one of these, answer what you can and say the rule
is undefined rather than inventing it:
- the exact "missed KPI" rule (relates to core_WellScope.kpi_days)
- whether a "lagging WBS branch" should come from core_ProgressDetail's wide columns or the
  core_ScheduleTask hierarchy, and what the numeric suffixes in those column names weigh.
"""

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
11. CRITICAL - every table you reference anywhere in a query (SELECT, WHERE, GROUP BY,
    ORDER BY, HAVING) MUST be brought in by that query's FROM or JOIN clause. Never mention
    a table you did not join. If you need a column from another table, add the join that
    reaches it through a real relationship - do not assume the column is reachable.
12. When an attribute lives on a table you have not joined, either join through the correct
    relationship chain, or select the equivalent column from a table you HAVE joined. Check
    every column reference resolves to a table in the same query scope, including inside
    each CTE (a CTE only sees the tables it declares itself).
12b. RELATIONSHIP DIRECTION: a foreign key exists on ONE side only. The child table holds the
    key that points at the parent's primary key; the parent does NOT hold a column pointing
    back at the child. Join on the parent's primary key = the child's foreign key. Before
    writing any join condition, confirm BOTH column names appear on the tables you name -
    read the schema, do not assume a mirrored column exists.
"""

# --------------------------------------------------------------------------------------
# 3. Correctness of values, time and units
# --------------------------------------------------------------------------------------
_CORRECTNESS_RULES = """
[AL-TASNIM] CORRECTNESS OF VALUES, TIME AND UNITS:
13. For "latest", "current" or "most recent", select the latest record per entity using the
    correct date/sequence column (ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ... DESC) or
    MAX()). Do NOT average across history when the user asked for the current value.
14. Treat snapshot/history tables as time series: pick the newest row per entity for current
    state, and use the full series only for trends.
15. NEVER treat NULL or missing data as zero. Distinguish zero vs missing vs unavailable vs
    not-applicable. A NULL date means "unknown", not 0 - never compute a delay, average or
    count as if NULL were 0.
16. Validate the meaning and scale of numeric fields before calculating. Progress-style
    values stored as decimals between 0 and 1 are fractions (0.27 = 27%) - multiply by 100
    for a percentage; do not mix fraction and percentage columns in one calculation.
17. Integer date keys in YYYYMMDD form must be converted to a real date before date maths,
    and only when the value is a valid, positive key.
18. Do totals, averages, counts and rankings in SQL (SUM/AVG/COUNT/MIN/MAX with GROUP BY,
    or window functions) so the database computes them exactly - never by eyeballing rows.
"""

# --------------------------------------------------------------------------------------
# 4. Dialect safety - the observed DATE_TRUNC class of failures
# --------------------------------------------------------------------------------------
_DIALECT_RULES = """
[AL-TASNIM] FUNCTION SAFETY:
19. You are writing SQL for Wren AI's query engine, which rewrites it for the target
    database. Do NOT assume the target database's native dialect. In particular, do not
    reach for vendor-specific spellings such as DATEADD, DATENAME, MONTH(), DAY(), CONVERT()
    or TOP(n) unless they appear in the SQL FUNCTIONS list you were given.
20. The SQL FUNCTIONS section of your input is the AUTHORITATIVE list of what the engine
    accepts. Use ONLY functions from that list. If the function you want is not there,
    express the same result with functions that ARE listed - never invent or substitute a
    similarly named one.
21. Use each function's exact argument signature; a wrong argument count is rejected at
    planning time. When unsure of a signature, prefer a simpler formulation you are certain
    of, built from listed functions.
22. Prefer clear, traceable SQL over clever SQL. Use CTEs for multi-step logic so each step
    can be checked.
23. Check column data types before comparing or joining. Never join or compare a text column
    to a numeric one, and never place a non-numeric value where a number is expected - an
    implicit conversion will fail at runtime. Join on the key columns the schema defines, and
    cast explicitly (with a listed function) only when the schema genuinely requires it.
"""

# --------------------------------------------------------------------------------------
# 5. Safety - read-only, always
# --------------------------------------------------------------------------------------
_SAFETY_RULES = """
[AL-TASNIM] READ-ONLY SAFETY:
24. Generate a SINGLE read-only SELECT statement (a WITH ... SELECT is fine). Never generate
    INSERT, UPDATE, DELETE, MERGE, DROP, ALTER, CREATE, TRUNCATE, EXEC, or multiple
    statements. Anything else is rejected before execution.
25. Never query internal application, configuration, credential, telemetry or staging
    tables, and never select any password/secret/token column, even if asked.
"""

# --------------------------------------------------------------------------------------
# 6. Self-check before returning SQL
# --------------------------------------------------------------------------------------
_SELF_CHECK_RULES = """
[AL-TASNIM] SELF-CHECK BEFORE RETURNING THE SQL:
26. Verify: does this answer the user's exact question? Are the tables and columns real and
    joined through their real relationships? Is latest-vs-historical handled correctly? Are
    units and scale right? Are there any filters, joins or DISTINCT that were not requested?
27. If the question is genuinely ambiguous or depends on a business definition you have not
    been given, do not invent one - answer the most reasonable literal reading and state the
    assumption, or ask for clarification.
"""

# --------------------------------------------------------------------------------------
# 7. Complex and multi-part questions - compose ONE query, never give up
# --------------------------------------------------------------------------------------
_COMPLEX_RULES = """
[AL-TASNIM] COMPLEX AND MULTI-PART QUESTIONS - always produce a query:
28. You get exactly ONE query per question, so a multi-part question must be answered by a
    SINGLE composed statement. Never refuse, never answer only one part, and never ask the
    user to split the question.
29. Build it with CTEs: give each part of the question its own WITH block, then combine.
    - Parts about the SAME entity -> join the CTEs on the shared key.
    - Independent lists ("list the wells AND the top 5 activity groups") -> UNION ALL the
      CTEs into one result, with a constant label column (e.g. 'well' / 'wbs') identifying
      which part each row belongs to, and NULL-padded columns so both parts share a shape.
    - Per-part limits apply inside that part's CTE (e.g. TOP (5) inside the WBS CTE only),
      never to the whole statement.
30. "Which X have more than one Y" style questions: collect the distinct X-Y pairs in a CTE
    (UNION the sources if the relationship exists on several tables), then GROUP BY X with
    HAVING COUNT(DISTINCT Y) > 1. Do not assume a single source table holds the whole
    relationship.
31. To show several related values per row (e.g. "the three projects for each well"), rank
    them with ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...) inside a CTE and pivot with
    conditional aggregation - never with an arbitrary MAX() over a descriptive column.
32. If a needed table is missing from the schema you were given, work with what you have and
    answer the part you can; do not invent tables or abandon the query.
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
    return domain_rules_enabled()


def sql_generation_rules() -> list[str]:
    """High-priority rules injected into every SQL generation prompt."""
    if not _enabled():
        return []
    return [
        # Business definitions first: they are the ground truth everything else builds on.
        _BUSINESS_RULES.strip(),
        _INTENT_RULES.strip(),
        _SCHEMA_RULES.strip(),
        _CORRECTNESS_RULES.strip(),
        _DIALECT_RULES.strip(),
        _COMPLEX_RULES.strip(),
        _SAFETY_RULES.strip(),
        _SELF_CHECK_RULES.strip(),
    ]


def business_definitions() -> str:
    """The Al-Tasnim domain definitions, for prompts that need intent grounding."""
    if not _enabled():
        return ""
    return _BUSINESS_RULES.strip()


def answer_integrity_rules() -> str:
    """Rules appended to the SQL-to-answer system prompt."""
    if not _enabled():
        return ""
    return _ANSWER_RULES.strip()


def domain_instructions() -> list[str]:
    """Alias kept for readability at call sites."""
    return sql_generation_rules()
