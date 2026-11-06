from tests.unit.ml.ml_group import require

pd = require("pandas")

from ml.dataset import RUN_COLUMNS, load_runs  # noqa: E402


def test_load_runs_with_no_agencies_returns_an_empty_typed_frame_without_a_client_call():
    frame = load_runs(None, [])
    assert list(frame.columns) == RUN_COLUMNS
    assert frame.empty
