"""The open-data source an agency's figures are credited to, for the CSVs the
server writes.

The source list and the credit wording each have one home, in the frontend:
``frontend/src/components/dataSources.json`` holds each source's feeds and
links, and the ``credits`` block of ``frontend/src/i18n/locales/{ja,en}.json``
holds each credit sentence and the ``link_with_url`` pattern. Both are read
here rather than copied, so a CSV credits the same sources in the same words
as the help page, the terms and a printed page. The deploy image copies
``frontend/src`` next to this package, and both files are read at import so a
missing one fails the app's start instead of a download.
"""

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_FRONTEND_SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
_SOURCES: list[dict[str, Any]] = json.loads(
    (_FRONTEND_SRC / "components" / "dataSources.json").read_text(encoding="utf-8")
)
_CREDITS: dict[str, dict[str, str]] = {
    lang: json.loads((_FRONTEND_SRC / "i18n" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))["credits"]
    for lang in ("ja", "en")
}
# A `<tag>name</tag>` link in a credit sentence, as the frontend's Trans renders it.
_LINKED_NAME = re.compile(r"<([a-z]+)>(.*?)</\1>")


def source_of(feed_url: str | None) -> dict[str, Any] | None:
    """The source *feed_url* is published under: one of its feed locations
    names the URL's host and, when it has one, a prefix of its path. None for
    a URL no source covers or that does not parse. The same match as
    ``dataSourceOf`` in ``frontend/src/components/dataSources.ts``."""
    if not feed_url:
        return None
    try:
        parts = urlsplit(feed_url)
    except ValueError:
        return None
    path = parts.path or "/"
    for source in _SOURCES:
        for feed in source["feeds"]:
            if parts.hostname == feed["host"] and path.startswith(feed.get("pathPrefix", "/")):
                return source
    return None


def credit_line(feed_url: str | None, locale: str) -> str | None:
    """The credit for *feed_url*'s source as plain text in *locale* (Japanese
    for any other), each linked name followed by its URL: a CSV cell keeps no
    link, and CC BY asks that the material and the licence be linked.
    ``creditText`` in ``dataSources.ts`` renders a printed page's credit the
    same way. None when no source covers the feed."""
    source = source_of(feed_url)
    if source is None:
        return None
    lang = locale if locale in _CREDITS else "ja"
    credits = _CREDITS[lang]
    links = {tag: href.replace("{locale}", lang) for tag, href in source["links"].items()}

    def written_out(link: re.Match[str]) -> str:
        return credits["link_with_url"].replace("{{name}}", link[2]).replace("{{url}}", links[link[1]])

    return _LINKED_NAME.sub(written_out, credits[source["creditKey"].removeprefix("credits.")])
