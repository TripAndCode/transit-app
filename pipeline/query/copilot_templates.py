"""Proactive-insight template registry.

Each template's LLM-facing contract is: the model may only choose a
``template_id`` plus small enum/const params (see ``param_schema``) — it
never emits a number. ``render()`` is pure application code that
interpolates every digit directly from the caller's already-fetched
``view_payload``, so a numeric hallucination has no code path to reach the
rendered text (spec: "Hallucination tolerance: zero for numbers, by
construction").

User-visible wording lives in :data:`pipeline.query.tools._LOCALES` like every
other server-side string, resolved through ``_summary``; ``render`` only picks
the key and supplies the payload numbers. An unsupported locale falls back to
``ja`` there rather than raising.
"""

from __future__ import annotations

from typing import Callable, TypedDict

from pipeline.query.tools import _summary


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
        render: Callable[[dict, dict, str], RenderedInsight],
    ) -> None:
        self.id = id
        self.tab = tab
        self.param_schema = param_schema
        self.render = render


def _render_overview_top_delay_route(params: dict, payload: dict, locale: str) -> RenderedInsight:
    routes = payload["top_delayed"]["routes"]
    if not routes:
        raise KeyError("top_delayed.routes is empty")
    top = routes[0]
    headline = payload["headline"]
    # The sign is carried by the localized direction word, so the percentage is
    # rendered unsigned — otherwise "-12.3% down" would double-state it.
    direction_key = "copilot_delta_up" if headline["delta_pct"] >= 0 else "copilot_delta_down"
    text = _summary(
        "copilot_overview_top_delay",
        locale,
        name=top["route_short_name"] or top["route_code"],
        top_avg=f"{top['avg_min']:g}",
        avg=f"{headline['avg_min']:g}",
        baseline=f"{headline['baseline_avg_min']:g}",
        delta=f"{abs(headline['delta_pct']):g}",
        direction=_summary(direction_key, locale),
        delayed_count=payload["top_delayed"]["delayed_count"],
    )
    cite = _summary("copilot_overview_top_delay_cite", locale, samples=headline["samples"])
    return {"text": text, "cite": cite}


def _render_no_signal(params: dict, payload: dict, locale: str) -> RenderedInsight:
    return {
        "text": _summary("copilot_no_signal", locale),
        "cite": _summary("copilot_no_signal_cite", locale),
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


def render_template(
    template_id: str, params: dict, view_payload: dict, locale: str = "ja"
) -> RenderedInsight:
    return TEMPLATES[template_id].render(params, view_payload, locale)


def templates_for_tab(tab: str) -> list[Template]:
    return [t for t in TEMPLATES.values() if t.tab in (tab, "*")]
