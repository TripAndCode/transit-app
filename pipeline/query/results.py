"""Shared result type for the Ask tool surface.

Extracted into a leaf module so both :mod:`pipeline.query.tools` and
:mod:`pipeline.query.meta_tools` can import ``ToolResult`` without a
circular import (tools imports meta_tools's specs at module load; both
need the result type).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pipeline.reports.definition import DefinitionMeta


@dataclass
class ToolResult:
    """Discriminated union (by ``kind``) returned to the API layer."""

    kind: Literal["table", "series", "kv", "empty", "text"]
    summary: str
    rows: list = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    series: list = field(default_factory=list)
    pairs: list = field(default_factory=list)
    # The method stamp the report computing these figures carries, built
    # before them over the scope they were computed for; None when no report
    # computes them.
    definition: DefinitionMeta | None = None
