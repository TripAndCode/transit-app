"""Collection and "nothing to show" endpoints answer with an object envelope.

A bare JSON array or a bare ``null`` is a shape with nowhere to grow: adding
paging, a total, or a "why it is empty" reason to one of them is a breaking
change for every client at once. Each endpoint below therefore wraps its
payload in a named field, and its response model says so.
"""

from pydantic import BaseModel

from tests.unit.test_response_schema_ratchet import _schema_routes

# endpoint -> the single field its payload lives under.
ENVELOPED = {
    "GET /api/{agency_id}/routes": "rows",
    "GET /api/{agency_id}/stops": "rows",
    "GET /api/{agency_id}/ask/suggest": "rows",
    "GET /api/{agency_id}/reports/suggest": "suggestion",
}


def _response_models() -> dict[str, object]:
    return {name: route.response_model for name, route in _schema_routes()}


def test_enveloped_endpoints_return_a_model_not_a_bare_array_or_null():
    models = _response_models()
    for endpoint, field in ENVELOPED.items():
        assert endpoint in models, f"{endpoint} is no longer registered"
        model = models[endpoint]
        assert isinstance(model, type) and issubclass(model, BaseModel), (
            f"{endpoint} answers with {model!r}; a list[...] or an optional model is a bare "
            "array/null on the wire — wrap it in a model instead"
        )
        assert field in model.model_fields, f"{endpoint}'s payload must live under {field!r}"


def test_reports_suggest_envelope_carries_the_absent_case_inside_it():
    """'No suggestion right now' is a populated 200 body, not a null one."""
    model = _response_models()["GET /api/{agency_id}/reports/suggest"]
    assert model.model_fields["suggestion"].is_required() is False
