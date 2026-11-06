from datetime import date

from tests.unit.ml.ml_group import require

pd = require("pandas")

from ml.dataset import RUN_COLUMNS, load_runs  # noqa: E402


def test_load_runs_with_no_agencies_returns_an_empty_typed_frame_without_a_client_call():
    frame = load_runs(None, [])
    assert list(frame.columns) == RUN_COLUMNS
    assert frame.empty


def test_fetch_runs_starts_its_weekly_reads_at_since(monkeypatch):
    from ml import dataset

    monkeypatch.setattr(dataset, "date_span", lambda client, agency_id: (date(2026, 1, 1), date(2026, 3, 31)))

    class Client:
        def __init__(self):
            self.starts = []

        def query_df(self, sql, parameters):
            self.starts.append(parameters["start"])
            return pd.DataFrame({column: [] for column in [*RUN_COLUMNS[:6], "mean_delay_min", "stops", "span_min"]})

    client = Client()
    load_runs(client, [8], since=date(2026, 3, 20))
    assert client.starts == [date(2026, 3, 20), date(2026, 3, 27)]
