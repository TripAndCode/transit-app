"""Write the method changelog into the methodology documents from METHOD_VERSIONS.

docs/methodology.md (Japanese) and docs/methodology.en.md (English) each hold
the changelog between START and END. This script is its only writer, and
tests/unit/test_methodology_doc.py fails while either block differs from what
render() makes of pipeline.agg_registry.METHOD_VERSIONS.

Usage:
    poetry run python scripts/render_method_changelog.py          # rewrite both blocks
    poetry run python scripts/render_method_changelog.py --check  # exit 1 if either is stale
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.agg_registry import METHOD_VERSIONS, MethodVersion  # noqa: E402  (sys.path injection above)

DOCS: dict[str, pathlib.Path] = {"ja": ROOT / "docs" / "methodology.md", "en": ROOT / "docs" / "methodology.en.md"}
START = "<!-- method-changelog:start -->"
END = "<!-- method-changelog:end -->"
COMMAND = "poetry run python scripts/render_method_changelog.py"

_WORDS: dict[str, dict[str, str]] = {
    "ja": {
        "note": f"<!-- {COMMAND} が METHOD_VERSIONS から書く。手で編集しない -->",
        "current": "現在の算出方法は `{version}` です。新しい版から順に、各版が設定した入力を示します。",
        "header": "| 版 | 設定した入力 |",
        "retired": "廃止",
    },
    "en": {
        "note": f"<!-- Written by {COMMAND} from METHOD_VERSIONS. Do not edit by hand. -->",
        "current": "The current method is `{version}`. Newest first, each version with the inputs it set.",
        "header": "| Version | Inputs set |",
        "retired": "retired",
    },
}


def _changes(entry: MethodVersion, before: dict[str, int], locale: str) -> str:
    """What *entry* set, against the inputs in force *before* it: each input
    with its new value, and the old one when it had one."""
    parts = []
    for name, value in sorted(entry.sets.items()):
        new = _WORDS[locale]["retired"] if value is None else str(value)
        parts.append(f"`{name}` {before[name]} → {new}" if name in before else f"`{name}` = {new}")
    return ", ".join(parts)


def render(locale: str, history: tuple[MethodVersion, ...] = METHOD_VERSIONS) -> str:
    """The changelog block for *locale*, markers included."""
    words = _WORDS[locale]
    rows = []
    inputs: dict[str, int] = {}
    for entry in history:
        rows.append(f"| `{entry.version}` | {_changes(entry, inputs, locale)} |")
        for name, value in entry.sets.items():
            if value is None:
                inputs.pop(name, None)
            else:
                inputs[name] = value
    return "\n".join(
        [
            START,
            words["note"],
            "",
            words["current"].format(version=history[-1].version),
            "",
            words["header"],
            "|---|---|",
            *reversed(rows),
            "",
            END,
        ]
    )


def section(text: str) -> str | None:
    """The changelog block in a document's *text*, markers included, or None
    when it has no complete one."""
    start = text.find(START)
    end = text.find(END, start)
    if start < 0 or end < 0:
        return None
    return text[start : end + len(END)]


def rewritten(text: str, locale: str) -> str:
    """*text* with its changelog block replaced by the current one."""
    block = section(text)
    if block is None:
        raise ValueError(f"no {START} … {END} block to write the changelog into")
    return text.replace(block, render(locale), 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report stale documents instead of rewriting them")
    args = parser.parse_args()
    stale = []
    for locale, path in DOCS.items():
        text = path.read_text(encoding="utf-8")
        current = rewritten(text, locale)
        if current == text:
            continue
        stale.append(path.relative_to(ROOT))
        if not args.check:
            path.write_text(current, encoding="utf-8")
    if args.check and stale:
        print(f"stale method changelog in {', '.join(map(str, stale))}: run `{COMMAND}`", file=sys.stderr)
        return 1
    for path in [] if args.check else stale:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
