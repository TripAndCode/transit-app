import io
from datetime import date, datetime, timezone
from urllib.error import URLError

import pytest

from pipeline.archive_store import ObjectStore, archive_keys, sign_request

# AWS's published Signature Version 4 examples for S3 ("Signature Calculations
# for the Authorization Header: Transferring Payload in a Single Chunk"). The
# signature depends on the secret, not the key id, which only names it.
_AWS_KEY_ID = "example-key-id"
_AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
_AWS_NOW = datetime(2013, 5, 24, tzinfo=timezone.utc)


def _signature(headers: dict[str, str]) -> str:
    return headers["Authorization"].rsplit("Signature=", 1)[1]


def test_signature_matches_the_aws_get_object_example():
    headers = sign_request(
        "GET",
        "https://examplebucket.s3.amazonaws.com/test.txt",
        access_key_id=_AWS_KEY_ID,
        secret_access_key=_AWS_SECRET,
        region="us-east-1",
        now=_AWS_NOW,
        extra_headers={"Range": "bytes=0-9"},
    )
    assert _signature(headers) == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date," in headers["Authorization"]
    assert headers["x-amz-date"] == "20130524T000000Z"


def test_signature_matches_the_aws_list_objects_example():
    headers = sign_request(
        "GET",
        "https://examplebucket.s3.amazonaws.com/?max-keys=2&prefix=J",
        access_key_id=_AWS_KEY_ID,
        secret_access_key=_AWS_SECRET,
        region="us-east-1",
        now=_AWS_NOW,
    )
    assert _signature(headers) == "34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7"


def test_signature_does_not_depend_on_the_order_headers_are_given_in():
    def sign(extra):
        return _signature(
            sign_request(
                "GET",
                "https://examplebucket.s3.amazonaws.com/test.txt",
                access_key_id=_AWS_KEY_ID,
                secret_access_key=_AWS_SECRET,
                region="us-east-1",
                now=_AWS_NOW,
                extra_headers=extra,
            )
        )

    assert sign({"Range": "bytes=0-9", "Accept": "*/*"}) == sign({"Accept": "*/*", "Range": "bytes=0-9"})


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _listing(keys: list[str], token: str | None) -> bytes:
    contents = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in keys)
    more = f"<IsTruncated>true</IsTruncated><NextContinuationToken>{token}</NextContinuationToken>" if token else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">{contents}{more}</ListBucketResult>'
    ).encode()


def _store(opener) -> ObjectStore:
    return ObjectStore(
        endpoint="https://account.r2.cloudflarestorage.com",
        bucket="transit-archives",
        access_key_id="id",
        secret_access_key="secret",
        opener=opener,
    )


def test_list_keys_follows_continuation_tokens_and_signs_every_page():
    pages = [_listing(["rt/8/20260601.tar.gz"], "t/1+"), _listing(["rt/8/20260602.tar.gz"], None)]
    seen = []

    def opener(request, timeout):
        seen.append(request)
        return _Response(pages[len(seen) - 1])

    keys = _store(opener).list_keys("rt/8/")

    assert keys == ["rt/8/20260601.tar.gz", "rt/8/20260602.tar.gz"]
    assert seen[0].full_url == "https://account.r2.cloudflarestorage.com/transit-archives?list-type=2&prefix=rt%2F8%2F"
    assert "continuation-token=t%2F1%2B" in seen[1].full_url
    assert all(r.get_header("Authorization", "").startswith("AWS4-HMAC-SHA256 ") for r in seen)


def test_download_retries_a_transient_failure_and_leaves_no_partial_file_when_it_gives_up(tmp_path):
    calls = []

    def flaky(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise URLError("reset")
        return _Response(b"archive bytes")

    dest = tmp_path / "20260601.tar.gz"
    _store(flaky).download("rt/8/20260601.tar.gz", dest, retry_wait=0)
    assert dest.read_bytes() == b"archive bytes"
    assert calls[0] == "https://account.r2.cloudflarestorage.com/transit-archives/rt/8/20260601.tar.gz"

    def broken(request, timeout):
        raise URLError("down")

    lost = tmp_path / "20260602.tar.gz"
    with pytest.raises(URLError):
        _store(broken).download("rt/8/20260602.tar.gz", lost, retry_wait=0)
    assert list(tmp_path.iterdir()) == [dest]


def test_from_env_names_every_missing_variable_and_no_value(monkeypatch):
    monkeypatch.setenv("OBJECT_STORE_ENDPOINT", "https://account.r2.cloudflarestorage.com")
    monkeypatch.setenv("OBJECT_STORE_ACCESS_KEY_ID", "visible-id-value")
    monkeypatch.delenv("OBJECT_STORE_BUCKET", raising=False)
    monkeypatch.delenv("OBJECT_STORE_SECRET_ACCESS_KEY", raising=False)

    with pytest.raises(RuntimeError) as exc:
        ObjectStore.from_env()

    message = str(exc.value)
    assert "OBJECT_STORE_BUCKET" in message
    assert "OBJECT_STORE_SECRET_ACCESS_KEY" in message
    assert "visible-id-value" not in message


def test_the_endpoint_must_be_https():
    with pytest.raises(ValueError, match="https"):
        ObjectStore(
            endpoint="http://account.r2.cloudflarestorage.com", bucket="b", access_key_id="i", secret_access_key="s"
        )


def test_archive_keys_keeps_the_agencys_daily_tarballs_within_the_range_in_day_order():
    keys = [
        "rt/8/20260603.tar.gz",
        "rt/8/20260601.tar.gz",
        "rt/8/20260602.tar.gz",
        "rt/8/20260602.tar.gz.part",
        "rt/8/notes.txt",
        "rt/80/20260602.tar.gz",
    ]

    assert archive_keys(keys, 8, since=date(2026, 6, 2), until=None) == [
        (date(2026, 6, 2), "rt/8/20260602.tar.gz"),
        (date(2026, 6, 3), "rt/8/20260603.tar.gz"),
    ]
    assert archive_keys(keys, 8, since=None, until=date(2026, 6, 1)) == [(date(2026, 6, 1), "rt/8/20260601.tar.gz")]
