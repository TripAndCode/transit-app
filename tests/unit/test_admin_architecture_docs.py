"""Pure-logic tests for `api.routers.admin`'s feature-doc helpers (item 25):
`_feature_doc_title` (title derivation, no filesystem/DB) and
`_list_feature_docs` (real filesystem glob against this repo's own
`docs/features/`, still no DB). Lives under `tests/unit/` per CLAUDE.md's
"pure logic tests bypass DB fixtures" convention -- `tests/unit/conftest.py`
overrides the session-scoped `apply_schema` autouse fixture so these don't
need a reachable Postgres at all.
"""

from __future__ import annotations

from pathlib import Path

from api.routers import admin
from api.routers.admin import _feature_doc_title, _has_real_content, _list_feature_docs


def test_feature_doc_title_uses_leading_h1():
    assert _feature_doc_title("# Ask tab\n\nSome body text.\n", "fallback-slug") == "Ask tab"


def test_feature_doc_title_skips_leading_blank_lines():
    assert _feature_doc_title("\n\n  # Map tab\nbody", "fallback-slug") == "Map tab"


def test_feature_doc_title_falls_back_when_first_line_not_h1():
    # First non-blank line is an HTML comment, not a `# ` heading -- never
    # raises, falls back to the slug instead of guessing.
    assert _feature_doc_title("<!-- a comment -->\n# Real Title\n", "fallback-slug") == "fallback-slug"


def test_feature_doc_title_falls_back_on_empty_file():
    assert _feature_doc_title("", "fallback-slug") == "fallback-slug"
    assert _feature_doc_title("   \n\n  \n", "fallback-slug") == "fallback-slug"


def test_list_feature_docs_finds_real_repo_docs():
    """Enumeration is a live glob, not a hardcoded list -- these files exist
    in the actual repo tree (predate this item) and must be found."""
    slugs = {p.stem for p in _list_feature_docs()}
    assert "ask-tab" in slugs
    assert "map-tab" in slugs


def test_list_feature_docs_sorted_by_filename():
    paths = _list_feature_docs()
    assert paths == sorted(paths)


def test_list_feature_docs_skips_html_comment_only_placeholders(tmp_path, monkeypatch):
    """An HTML-comment-only `.md` file must never reach the admin UI."""
    (tmp_path / "real-tab.md").write_text("# Real Tab\n\nBody text.\n")
    (tmp_path / "placeholder-tab.md").write_text("<!-- just a comment -->\n")
    monkeypatch.setattr(admin, "_FEATURE_DOCS_DIR", Path(tmp_path))

    slugs = {p.stem for p in _list_feature_docs()}
    assert slugs == {"real-tab"}


def test_has_real_content_true_for_normal_markdown():
    assert _has_real_content("# Title\n\nBody text.\n") is True


def test_has_real_content_false_for_comment_only():
    assert _has_real_content("<!-- just a comment -->\n") is False


def test_has_real_content_false_for_empty_or_whitespace():
    assert _has_real_content("") is False
    assert _has_real_content("   \n\n  \n") is False


def test_has_real_content_true_when_comment_plus_real_text():
    assert _has_real_content("<!-- note -->\n# Title\nBody\n") is True
