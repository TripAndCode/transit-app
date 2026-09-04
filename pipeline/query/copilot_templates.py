"""Proactive-insight template registry.

Each template's LLM-facing contract is: the model may only choose a
``template_id`` plus small enum/const params (see ``param_schema``) — it
never emits a number. ``render()`` is pure application code that
interpolates every digit directly from the caller's already-fetched
``view_payload``, so a numeric hallucination has no code path to reach the
rendered text (spec: "Hallucination tolerance: zero for numbers, by
construction").
"""

from __future__ import annotations

from typing import Callable, TypedDict


class RenderedInsight(TypedDict):
    text: str
    cite: str


class Template:
    __slots__ = ("id", "param_schema", "render", "tab")

    def __init__(
        self,
        id: str,
        tab: str,
        param_schema: dict,
        render: Callable[[dict, dict], RenderedInsight],
    ) -> None:
        self.id = id
        self.tab = tab
        self.param_schema = param_schema
        self.render = render


def _render_overview_top_delay_route(params: dict, payload: dict) -> RenderedInsight:
    routes = payload["top_delayed"]["routes"]
    if not routes:
        raise KeyError("top_delayed.routes is empty")
    top = routes[0]
    headline = payload["headline"]
    name = top["route_short_name"] or top["route_code"]
    text = (
        f"Route {name} is running the longest average delay right now, "
        f"at {top['avg_min']:g} min. Across all routes the average is "
        f"{headline['avg_min']:g} min this week, up {headline['delta_pct']:g}% "
        f"from the {headline['baseline_avg_min']:g} min baseline "
        f"({payload['top_delayed']['delayed_count']} routes currently delayed)."
    )
    cite = f"Overview · {headline['samples']} samples · top_delayed[0]"
    return {"text": text, "cite": cite}


def _render_no_signal(params: dict, payload: dict) -> RenderedInsight:
    return {
        "text": "Nothing stands out from the current view — delays look broadly typical for this range.",
        "cite": "no notable pattern",
    }


NO_SIGNAL_TEMPLATE_ID = "no_signal"

TEMPLATES: dict[str, Template] = {
    "overview_top_delay_route": Template(
        id="overview_top_delay_route",
        tab="overview",
        param_schema={"type": "object", "properties": {}, "additionalProperties": False},
        render=_render_overview_top_delay_route,
    ),
    NO_SIGNAL_TEMPLATE_ID: Template(
        id=NO_SIGNAL_TEMPLATE_ID,
        tab="*",
        param_schema={"type": "object", "properties": {}, "additionalProperties": False},
        render=_render_no_signal,
    ),
}


def render_template(template_id: str, params: dict, view_payload: dict) -> RenderedInsight:
    return TEMPLATES[template_id].render(params, view_payload)


def templates_for_tab(tab: str) -> list[Template]:
    return [t for t in TEMPLATES.values() if t.tab in (tab, "*")]
