"""Proactive-insight generation for the Copilot side panel.

The LLM call here only ever selects a ``template_id`` (+ small enum params)
via a forced tool call — it never sees or emits the rendered text's numbers.
See ``pipeline.query.copilot_templates`` for the actual interpolation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from pipeline.query.copilot_templates import (
    NO_SIGNAL_TEMPLATE_ID,
    TEMPLATES,
    render_template,
    templates_for_tab,
)
from pipeline.query.llm_client import get_client

logger = logging.getLogger(__name__)


class NoInsightAvailable(Exception):
    """Raised when there is no `view_payload` or no template for this tab."""


def is_enabled() -> bool:
    """True iff the proactive-insight feature is turned on.

    Off by default: this is the only always-on, no-user-action LLM call in the
    app — the panel fires it on the default tab for every visitor — so it has
    to be opted into per deployment rather than shipped hot.
    """
    return os.environ.get("COPILOT_INSIGHT_ENABLED", "false").lower() in ("1", "true", "yes")


def _get_client():
    return get_client()


def _pick_template_tool(tab: str) -> dict:
    candidates = templates_for_tab(tab)
    fallback = TEMPLATES[NO_SIGNAL_TEMPLATE_ID]
    all_candidates = candidates if any(t.id == fallback.id for t in candidates) else [*candidates, fallback]
    template_ids = [t.id for t in all_candidates]

    # Union each candidate's own param_schema into the tool's params schema, so the
    # model is bounded by every registered template's declared enum/const params —
    # not left free to emit arbitrary fields, which would defeat the templates'
    # own numeric-hallucination-safety contract (see module docstring).
    params_properties: dict = {}
    additional_properties_allowed = False
    for t in all_candidates:
        params_properties.update(t.param_schema.get("properties", {}))
        if t.param_schema.get("additionalProperties", True):
            additional_properties_allowed = True

    return {
        "type": "function",
        "function": {
            "name": "pick_template",
            "description": "Choose which pre-verified insight template best matches the current data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_id": {"type": "string", "enum": sorted(set(template_ids))},
                    "params": {
                        "type": "object",
                        "properties": params_properties,
                        "additionalProperties": additional_properties_allowed,
                    },
                },
                "required": ["template_id", "params"],
            },
        },
    }


async def generate_proactive_insight(
    tab: str, filters: dict, view_payload: dict, *, locale: str = "ja"
) -> dict:
    if not view_payload:
        raise NoInsightAvailable(f"no view_payload for tab={tab!r}")
    if not any(t.tab == tab for t in templates_for_tab(tab)):
        raise NoInsightAvailable(f"no templates registered for tab={tab!r}")

    tool = _pick_template_tool(tab)
    client = _get_client()
    message, _error = await asyncio.to_thread(
        client.chat_completions,
        messages=[
            {
                "role": "system",
                "content": (
                    "You select which pre-verified insight template best matches the "
                    "given data summary. Call pick_template exactly once. Never invent numbers."
                ),
            },
            {"role": "user", "content": json.dumps({"tab": tab, "filters": filters, "data": view_payload})},
        ],
        tools=[tool],
        tool_choice="required",
        temperature=0.0,
    )

    template_id = NO_SIGNAL_TEMPLATE_ID
    params: dict = {}
    if message is not None and getattr(message, "tool_calls", None):
        try:
            args = json.loads(message.tool_calls[0].function.arguments)
            template_id = args.get("template_id", NO_SIGNAL_TEMPLATE_ID)
            params = args.get("params", {}) or {}
        except (json.JSONDecodeError, AttributeError, IndexError, TypeError):
            logger.warning("copilot: malformed tool_call, falling back to no_signal")

    if not any(t.id == template_id for t in templates_for_tab(tab)):
        logger.warning("copilot: LLM picked unknown template_id=%r, falling back", template_id)
        rendered = render_template(NO_SIGNAL_TEMPLATE_ID, {}, view_payload, locale)
    else:
        try:
            rendered = render_template(template_id, params, view_payload, locale)
        except (KeyError, TypeError, ValueError):
            logger.warning("copilot: template render failed for template_id=%r, falling back", template_id)
            rendered = render_template(NO_SIGNAL_TEMPLATE_ID, {}, view_payload, locale)

    low_confidence = bool(view_payload.get("low_confidence", False))
    return {"text": rendered["text"], "cite": rendered["cite"], "low_confidence": low_confidence}
