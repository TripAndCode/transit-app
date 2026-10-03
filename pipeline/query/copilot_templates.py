"""Proactive-insight template registry.

Each template states, as ``applies``, the condition on the caller's
already-fetched ``view_payload`` under which it is the insight to show;
:func:`select_template_id` picks the first one that holds. ``render()``
interpolates every digit directly from that same payload, so the rendered
text can only ever state numbers the caller already had.

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
    __slots__ = ("applies", "id", "render", "tab")

    def __init__(
        self,
        id: str,
        tab: str,
        render: Callable[[dict, str], RenderedInsight],
        applies: Callable[[dict], bool],
    ) -> None:
        self.id = id
        self.tab = tab
        self.render = render
        self.applies = applies


def _has_delayed_route(payload: dict) -> bool:
    top = payload.get("top_delayed")
    if not isinstance(top, dict):
        return False
    count = top.get("delayed_count")
    return bool(top.get("routes")) and isinstance(count, (int, float)) and count > 0


def _render_overview_top_delay_route(payload: dict, locale: str) -> RenderedInsight:
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
    cite = _summary("copilot_overview_top_delay_cite", locale, samples=f"{headline['samples']:,}")
    return {"text": text, "cite": cite}


def _render_no_signal(payload: dict, locale: str) -> RenderedInsight:
    return {
        "text": _summary("copilot_no_signal", locale),
        "cite": _summary("copilot_no_signal_cite", locale),
    }


NO_SIGNAL_TEMPLATE_ID = "no_signal"

TEMPLATES: dict[str, Template] = {
    # Names the longest-delay route and compares the all-route average with
    # its baseline.
    "overview_top_delay_route": Template(
        id="overview_top_delay_route",
        tab="overview",
        render=_render_overview_top_delay_route,
        applies=_has_delayed_route,
    ),
    # Says nothing stands out: the answer whenever no other template applies.
    NO_SIGNAL_TEMPLATE_ID: Template(
        id=NO_SIGNAL_TEMPLATE_ID,
        tab="*",
        render=_render_no_signal,
        applies=lambda payload: True,
    ),
}


def render_template(template_id: str, view_payload: dict, locale: str = "ja") -> RenderedInsight:
    return TEMPLATES[template_id].render(view_payload, locale)


def templates_for_tab(tab: str) -> list[Template]:
    return [t for t in TEMPLATES.values() if t.tab in (tab, "*")]


def select_template_id(tab: str, view_payload: dict) -> str:
    """The first of ``tab``'s own templates whose condition holds, else no-signal."""
    for template in templates_for_tab(tab):
        if template.id != NO_SIGNAL_TEMPLATE_ID and template.applies(view_payload):
            return template.id
    return NO_SIGNAL_TEMPLATE_ID
