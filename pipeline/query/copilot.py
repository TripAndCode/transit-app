"""Proactive-insight generation for the Copilot side panel.

The insight is one of :mod:`pipeline.query.copilot_templates`' canned
templates, chosen by each template's own condition on the caller's view
payload and filled with numbers taken from that same payload. No model is
involved, so the same payload always yields the same insight.
"""

from __future__ import annotations

import logging

from pipeline.flags import aflag, flag
from pipeline.query.copilot_templates import (
    NO_SIGNAL_TEMPLATE_ID,
    render_template,
    select_template_id,
    templates_for_tab,
)

logger = logging.getLogger(__name__)


class NoInsightAvailable(Exception):
    """Raised when there is no `view_payload` or no template for this tab."""


def is_enabled() -> bool:
    """True iff the proactive-insight feature is turned on.

    Off by default: the panel is opted into per deployment.
    """
    return flag("copilot_insight_enabled", False)


async def ais_enabled() -> bool:
    """:func:`is_enabled` for a caller on the event loop.

    After `invalidate()` the async read waits for the re-read, so the request
    right after an operator flips this switch sees the new value; the
    synchronous form answers it with the old one while the re-read runs
    behind it. With nothing cached at all, the synchronous form reads
    Postgres inline, which on the event loop stalls every other request.
    """
    return await aflag("copilot_insight_enabled")


async def generate_proactive_insight(tab: str, view_payload: dict, *, locale: str = "ja") -> dict:
    """Render the insight for ``tab`` from ``view_payload``.

    A template whose condition holds but whose render finds a field it needs
    missing falls back to the no-signal template rather than failing the
    request.
    """
    if not view_payload:
        raise NoInsightAvailable(f"no view_payload for tab={tab!r}")
    if not any(t.tab == tab for t in templates_for_tab(tab)):
        raise NoInsightAvailable(f"no templates registered for tab={tab!r}")

    template_id = select_template_id(tab, view_payload)
    try:
        rendered = render_template(template_id, view_payload, locale)
    except (KeyError, TypeError, ValueError):
        logger.warning("copilot: template render failed for template_id=%r, falling back", template_id)
        rendered = render_template(NO_SIGNAL_TEMPLATE_ID, view_payload, locale)

    low_confidence = bool(view_payload.get("low_confidence", False))
    return {"text": rendered["text"], "cite": rendered["cite"], "low_confidence": low_confidence}
