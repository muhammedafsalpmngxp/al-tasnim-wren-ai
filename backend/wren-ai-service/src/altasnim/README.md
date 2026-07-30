# Al-Tasnim customisations (`src/altasnim`)

This package layers the accuracy and safety guarantees of the standalone **Al-Tasnim
agentic chatbot** onto Wren AI, while keeping everything Wren AI is good at — chart
generation (Vega-Lite), the semantic model, the UI and its pipelines — untouched.

Everything is **additive** and can be switched off with environment variables.

## What it adds

| # | Capability | Where it comes from | Implementation |
|---|---|---|---|
| 1 | **SELECT-only enforcement** | chatbot `validator.py` | `guard.py`, called by every engine before SQL runs |
| 2 | **Domain + accuracy rules** | chatbot `prompts.py` | `domain_rules.py`, injected into every SQL generation prompt |
| 3 | **Answer integrity** | chatbot verifier agent | `domain_rules.py`, appended to the SQL-to-answer prompt |
| 4 | **Independent SQL verifier** | chatbot verifier agent | `pipelines/generation/altasnim_sql_verifier.py`, reviews SQL before it is accepted |

### 4. Independent SQL verifier

Wren AI's dry run only proves a query *executes* - it cannot tell whether the query answers
the right question. This adds the chatbot's second opinion as a real pipeline:

```
SQL generation -> dry run OK -> [Al-Tasnim verifier] -> accepted -> execute
                                        |
                                   rejected -> existing SQL-correction loop (with the
                                               verifier's feedback as the error)
```

It rejects a query when it references a table never joined (including inside a CTE), uses a
non-existent table/column, answers a different question, adds unrequested filters/DISTINCT,
ignores the requested result size, confuses latest-vs-history, misuses a function signature
(e.g. two-argument `DATEDIFF`), fakes grouping with `MAX(name)`, or is not a read-only SELECT.

Two safety properties:
* **Fails open** - any error or unparsable verdict approves the SQL, so verification can
  never break a working answer.
* **Reuses the proven fix path** - a rejection is handed to Wren AI's existing correction
  loop rather than a new one.

Cost: one extra LLM call per question (uses the same model as SQL generation). Disable with
`ALTASNIM_VERIFIER=false`.

### 1. Read-only guard (`guard.py`)
Deterministic code — not an LLM — so it cannot be prompt-injected. Rejects anything that
is not a single read-only `SELECT`/`WITH … SELECT`:

* DML/DDL: `INSERT UPDATE DELETE MERGE DROP ALTER CREATE TRUNCATE`
* Execution: `EXEC/EXECUTE`, `sp_*`, `xp_*`
* Privileges/ops: `GRANT REVOKE BACKUP RESTORE SHUTDOWN RECONFIGURE`
* `SELECT … INTO` (writes a table)
* Statement stacking (`SELECT 1; DROP TABLE x`), including comment-hidden statements

A blocked statement returns the engine's normal failure tuple with a clear
`error_message`, so the UI degrades gracefully instead of crashing.

### 2. Domain + accuracy rules (`domain_rules.py`)
Six rule groups injected ahead of the UI-managed Instructions (which still win when more
specific):

1. **Intent fidelity** – answer exactly what was asked; no unrequested filters/DISTINCT/columns; respect "all / top N / sample / count".
2. **Schema, joins and mappings** – only real tables/columns; follow real relationships; no useless joins; no `MAX(name)` group hacks.
3. **Correctness of values, time and units** – latest-vs-historical, NULL ≠ 0, fraction vs percentage, `YYYYMMDD` date keys, aggregate in SQL.
4. **Dialect safety** – SQL Server functions only; never `DATE_TRUNC`, `EXTRACT`, `ILIKE`, `LIMIT`, `::`.
5. **Read-only safety** – single SELECT; never touch config/credential/staging tables or secret columns.
6. **Self-check** – verify the SQL against the question before returning it.

### 3. Answer integrity (`domain_rules.py`)
Applied when results are turned into prose: every figure must trace to the returned data;
no fabricated totals/thresholds/scores; label estimates; separate facts from
interpretation; plain business language with no table/column names or raw flags.

## Configuration

| Env var | Default | Effect |
|---|---|---|
| `ALTASNIM_SELECT_ONLY` | `true` | `false` disables the read-only guard |
| `ALTASNIM_DOMAIN_RULES` | `true` | `false` disables rule + answer-integrity injection |
| `ALTASNIM_VERIFIER` | `true` | `false` disables the independent SQL verifier (saves 1 LLM call/question) |

Set them in `docker/.env` (they are read by `wren-ai-service`).

## Integration points (3 small, additive edits)

| File | Change |
|---|---|
| `src/providers/engine/wren.py` | `altasnim_reject()` helper + a 2-line guard at the top of `execute_sql` in `WrenUI`, `WrenIbis`, `WrenEngine` |
| `src/pipelines/generation/utils/sql.py` | `construct_instructions()` prepends the domain rules |
| `src/pipelines/generation/sql_answer.py` | answer-integrity rules appended to the system prompt |
| `src/pipelines/generation/__init__.py` | exports `AltasnimSqlVerifier` |
| `src/globals.py` | registers the verifier in the ask service |
| `src/web/v1/services/ask.py` | runs the verifier on a dry-run-valid query; a rejection is routed into the correction loop |

Because `construct_instructions()` is shared, the rules automatically apply to
`sql_generation`, `followup_sql_generation`, `sql_correction` and `sql_regeneration` —
and therefore to the SQL behind every chart.

## Applying changes

`wren-ai-service` normally runs from a prebuilt image, so Python changes require a rebuild:

```bash
cd docker
docker compose build wren-ai-service     # or: docker compose up -d --build wren-ai-service
docker compose --env-file .env up -d --force-recreate wren-ai-service
```

## Tests

```bash
cd backend/wren-ai-service
pytest tests/altasnim -q
```
