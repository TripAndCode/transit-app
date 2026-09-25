"""`GET /api/admin/ask/queries` returns raw `question` text verbatim, so its
data-handling facts (classification, retention) belong on the endpoint the
operator reads, not only in a migration comment nobody browsing the API sees.
No behavior changes here -- this pins the docstring content.
"""

from api.routers.admin_ask import list_ask_queries


def test_queries_endpoint_documents_question_retention_and_classification():
    doc = list_ask_queries.__doc__ or ""
    assert "Internal" in doc
    assert "90 days" in doc
    assert "prune-query-log" in doc
