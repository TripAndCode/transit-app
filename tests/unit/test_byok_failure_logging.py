"""A failed provider call must be diagnosable without logging key material.

These calls carry an API key — the user's own on the BYOK paths, the
operator's on the shared ladder — and a provider's error body routinely
echoes part of it back ("Incorrect API key provided: sk-..."). The exception's
message is therefore the one field that must never reach a log, which rules
out `exc_info=True`, `%s`/`%r` on the exception, `str(exc)`, and `exc.args`.

`describe_provider_failure` is the single sanctioned way to render one, so the
functional test below pins its behaviour and the AST check pins that no
handler bypasses it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pipeline.query.llm_client import describe_provider_failure

ROOT = Path(__file__).resolve().parents[2]
SECRET = "sk-live-thisistheusers-secret-key"

# Every module with an `except` handler that logs a failed provider call.
PROVIDER_CALL_MODULES = (
    "pipeline/query/llm_client.py",
    "pipeline/query/chat.py",
    "pipeline/query/copilot.py",
)


# Calls that send an API key to a provider. A handler only carries key-bearing
# error text if its own `try` made one of these; other handlers in these
# modules catch ordinary application errors whose messages are safe to log.
_PROVIDER_CALLS = {"_completion_with_key", "create", "chat_completions"}


def _provider_call_handlers(tree: ast.AST):
    """Every `except ... as e` guarding a provider call."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        # Any *reference*, not just a direct call: these are also handed to
        # asyncio.to_thread by name, in which case the provider call never
        # appears as a Call node with that function as its target.
        made_provider_call = any(
            (isinstance(n, ast.Name) and n.id in _PROVIDER_CALLS)
            or (isinstance(n, ast.Attribute) and n.attr in _PROVIDER_CALLS)
            for stmt in node.body
            for n in ast.walk(stmt)
        )
        if not made_provider_call:
            continue
        for handler in node.handlers:
            if handler.name:
                yield handler


class _ProviderError(Exception):
    """Shaped like an SDK error whose message came from the provider body."""

    def __init__(self) -> None:
        super().__init__(f"Incorrect API key provided: {SECRET}")
        self.status_code = 401
        self.request_id = "req_abc123"


def test_description_omits_the_message_and_keeps_the_useful_parts():
    described = describe_provider_failure(_ProviderError())
    assert SECRET not in described, "the provider's message reached the description, key and all"
    assert "_ProviderError" in described, "the type is what makes the failure diagnosable"
    assert "status=401" in described
    assert "request_id=req_abc123" in described


def test_description_tolerates_an_exception_carrying_neither_attribute():
    """A non-SDK exception has no status or request id; the type alone is
    still worth logging and must not raise on the way out."""
    assert describe_provider_failure(ValueError("boom")) == "ValueError"


@pytest.mark.parametrize("module_path", PROVIDER_CALL_MODULES)
def test_no_handler_hands_the_exception_to_a_logger(module_path: str):
    """The caught exception may only reach a logger via the shared helper.

    Checked on the syntax tree rather than the source text: the unsafe forms
    are many (`%r` on the exception, `str(exc)`, `repr(exc)`, `exc.args`,
    `f"{exc!r}"`, `exc_info=True`) and a substring search catches only
    whichever ones it was written against. Here any reference to the caught
    name inside a logging call is rejected unless it is the helper's argument.
    """
    tree = ast.parse((ROOT / module_path).read_text())

    for handler in _provider_call_handlers(tree):
        caught = handler.name
        for call in (n for n in ast.walk(handler) if isinstance(n, ast.Call)):
            func = call.func
            is_log_call = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and (func.value.id in {"_log", "logger", "logging"})
            )
            if not is_log_call:
                continue

            assert not any(kw.arg == "exc_info" for kw in call.keywords), (
                f"{module_path}: a provider-failure log attaches a traceback, whose text includes the message"
            )

            # Everything that provably cannot render the provider's message.
            safe = {
                f"describe_provider_failure({caught})",
                f"type({caught}).__name__",
                f"{caught}.__class__.__name__",
                f"{caught}.status_code",
                f"{caught}.request_id",
                f"getattr({caught}, 'status_code', None)",
                f"getattr({caught}, 'request_id', None)",
            }
            for arg in call.args:
                if ast.unparse(arg) in safe:
                    continue
                for node in ast.walk(arg):
                    if isinstance(node, ast.Name) and node.id == caught:
                        raise AssertionError(
                            f"{module_path}: `{caught}` reaches {ast.unparse(func)} as "
                            f"`{ast.unparse(arg)}`, which can render the provider's message and with it "
                            "the API key. Use describe_provider_failure()."
                        )
