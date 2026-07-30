"""Al-Tasnim independent SQL verifier.

Wren AI's dry-run only proves a query *executes*; it cannot tell whether the query
actually answers the user's question. This pipeline adds the second opinion that the
standalone Al-Tasnim chatbot had: a separate, skeptical reviewer that reads the question,
the schema and the generated SQL, and rejects work that is subtly wrong - a table used but
never joined, a filter the user never asked for, latest-vs-average confusion, a wrong
function signature, and so on.

A rejection is fed back into Wren AI's existing SQL-correction loop, so the fix path is
the one that is already proven, and nothing else in the ask flow changes.

Toggle with ALTASNIM_VERIFIER (default: enabled).
"""

import logging
import sys
from typing import Any

from hamilton import base
from hamilton.async_driver import AsyncDriver
from haystack.components.builders.prompt_builder import PromptBuilder
from langfuse.decorators import observe
from pydantic import BaseModel

from src.altasnim.settings import verifier_enabled
from src.core.pipeline import BasicPipeline
from src.core.provider import LLMProvider
from src.pipelines.common import clean_up_new_lines
from src.utils import trace_cost

logger = logging.getLogger("wren-ai-service")


sql_verifier_system_prompt = """
### TASK ###

You are an independent, strict SQL reviewer for a Microsoft SQL Server analytics platform.
You did NOT write the query under review, so you judge it with fresh eyes.

Your job is to decide whether the SQL genuinely and correctly answers the user's question,
given the database schema. You are the last line of defence before the query runs, so be
skeptical - but do not reject a query merely because you would have written it differently.

### REJECT THE QUERY IF ANY OF THESE ARE TRUE ###

1. SCOPE: it references a table anywhere (SELECT / WHERE / GROUP BY / ORDER BY / HAVING)
   that is not brought in by that query's own FROM or JOIN - including inside each CTE,
   which only sees the tables it declares itself.
2. SCHEMA: it uses a table or column that does not exist in the provided schema, or joins
   two tables on a relationship the schema does not support.
3. INTENT: it does not answer what was actually asked - wrong entity, wrong measure, or a
   different business concept substituted for the one requested.
4. UNREQUESTED FILTERS: it adds filters the user never asked for and that are not logically
   required (is_active, is_current, latest-record, date, status, IS NOT NULL), or adds
   DISTINCT that the question does not need.
5. RESULT SIZE: it ignores the requested size - "all" must not be capped with TOP; "top N"
   must return exactly N with a matching ORDER BY; "how many" must return a COUNT.
6. LATEST vs HISTORY: the question asks for current/latest state but the query averages or
   aggregates across history instead of selecting the newest row per entity (or vice versa).
7. FUNCTION MISUSE: a function is called with the wrong signature or does not exist in SQL
   Server - for example DATEDIFF must take three arguments, DATEDIFF(day, start, end), and
   DATE_TRUNC / EXTRACT / ILIKE / LIMIT do not exist.
8. GROUPING: an arbitrary MAX()/MIN() on a descriptive column is used just to satisfy
   GROUP BY, letting one arbitrary row stand in for a whole group.
9. SAFETY: it is not a single read-only SELECT (or WITH ... SELECT).

### OTHERWISE APPROVE ###

If none of the above apply, approve it. Style preferences, formatting, alternative but
equally valid joins, and extra-but-harmless ordering are NOT reasons to reject.

### OUTPUT FORMAT ###

Return ONLY this JSON:

{
    "ok": true or false,
    "issue": "<the single most important problem, empty string when ok>",
    "feedback": "<when ok is false: a concrete, actionable instruction telling the SQL author exactly how to fix it; empty string when ok>"
}
"""

sql_verifier_user_prompt_template = """
### DATABASE SCHEMA ###
{% for doc in documents %}
    {{ doc }}
{% endfor %}

### USER'S QUESTION ###
{{ query }}

{% if sql_generation_reasoning %}
### THE PLAN THE AUTHOR FOLLOWED ###
{{ sql_generation_reasoning }}
{% endif %}

### SQL UNDER REVIEW ###
{{ sql }}

Review the SQL against the question and the schema. Think step by step, then return only the
JSON verdict.
"""


class SqlVerifierResult(BaseModel):
    ok: bool
    issue: str
    feedback: str


SQL_VERIFIER_MODEL_KWARGS = {
    "response_format": {
        "type": "json_schema",
        "json_schema": {
            "name": "sql_verifier_result",
            "schema": SqlVerifierResult.model_json_schema(),
        },
    }
}


def is_verifier_enabled() -> bool:
    """The verifier costs one extra LLM call per question; allow it to be switched off."""
    return verifier_enabled()


## Start of Pipeline
@observe(capture_input=False)
def prompt(
    query: str,
    sql: str,
    documents: list[str],
    sql_generation_reasoning: str,
    prompt_builder: PromptBuilder,
) -> dict:
    _prompt = prompt_builder.run(
        query=query,
        sql=sql,
        documents=documents,
        sql_generation_reasoning=sql_generation_reasoning,
    )
    return {"prompt": clean_up_new_lines(_prompt.get("prompt"))}


@observe(as_type="generation", capture_input=False)
@trace_cost
async def verify_sql(prompt: dict, generator: Any, generator_name: str) -> dict:
    return await generator(prompt=prompt.get("prompt")), generator_name


@observe(capture_input=False)
def post_process(verify_sql: dict) -> dict:
    """Fail OPEN: any parsing/LLM problem approves the SQL rather than blocking the user."""
    import orjson

    default = {"ok": True, "issue": "", "feedback": ""}
    try:
        replies = verify_sql.get("replies") or []
        if not replies:
            return default
        result = orjson.loads(replies[0])
        return {
            "ok": bool(result.get("ok", True)),
            "issue": str(result.get("issue", "") or ""),
            "feedback": str(result.get("feedback", "") or ""),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("[altasnim-verifier] could not parse verdict, approving: %s", e)
        return default


## End of Pipeline


class AltasnimSqlVerifier(BasicPipeline):
    def __init__(
        self,
        llm_provider: LLMProvider,
        **kwargs,
    ):
        self._components = {
            "prompt_builder": PromptBuilder(template=sql_verifier_user_prompt_template),
            "generator": llm_provider.get_generator(
                system_prompt=sql_verifier_system_prompt,
                generation_kwargs=SQL_VERIFIER_MODEL_KWARGS,
            ),
            "generator_name": llm_provider.get_model(),
        }

        super().__init__(
            AsyncDriver({}, sys.modules[__name__], result_builder=base.DictResult())
        )

    @observe(name="Al-Tasnim SQL Verification")
    async def run(
        self,
        query: str,
        sql: str,
        contexts: list[str],
        sql_generation_reasoning: str = "",
    ) -> dict:
        logger.info("Al-Tasnim SQL Verifier is running...")
        return await self._pipe.execute(
            ["post_process"],
            inputs={
                "query": query,
                "sql": sql,
                "documents": contexts,
                "sql_generation_reasoning": sql_generation_reasoning or "",
                **self._components,
            },
        )
