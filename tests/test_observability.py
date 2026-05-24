import logging

from api.logging_config import REQUEST_ID_CTX, configure


def test_request_id_filter_default_dash():
    """A LogRecord emitted outside any request scope must still resolve
    request_id (to '-') so the format string never KeyErrors."""
    configure()
    logger = logging.getLogger("test.outside_request")
    rec = logger.makeRecord(
        name="test.outside_request",
        level=logging.INFO,
        fn="t",
        lno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    root = logging.getLogger()
    for f in root.handlers[0].filters:
        f.filter(rec)
    assert rec.request_id == "-"


def test_request_id_filter_reads_contextvar():
    configure()
    token = REQUEST_ID_CTX.set("ctx-test")
    try:
        rec = logging.getLogger("test").makeRecord(
            "test",
            logging.INFO,
            "t",
            1,
            "hi",
            (),
            None,
        )
        for f in logging.getLogger().handlers[0].filters:
            f.filter(rec)
        assert rec.request_id == "ctx-test"
    finally:
        REQUEST_ID_CTX.reset(token)


def test_configure_is_idempotent():
    """Calling configure() twice doesn't pile up root handlers."""
    configure()
    n1 = len(logging.getLogger().handlers)
    configure()
    n2 = len(logging.getLogger().handlers)
    assert n2 == n1
