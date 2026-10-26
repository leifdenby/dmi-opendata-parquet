"""Tests for the S3 bucket listing server."""

from __future__ import annotations

import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from dmi_geoparquet.serve import list_objects, make_server

NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


def _bucket(root: Path) -> Path:
    """Create a small Hive-partitioned tree with a status file and a temp file."""

    for month in ("01", "02"):
        path = root / "raw" / "year=2026" / f"month={month}" / "part-00000.parquet"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"PAR1")
    (root / "_status.json").write_text("{}")
    (root / "raw" / "year=2026" / "month=02" / ".part-00000.parquet.tmp").write_bytes(b"x")
    return root


def _parse(body: bytes) -> tuple[list[str], list[str], ET.Element]:
    """Return keys, common prefixes and the parsed document."""

    doc = ET.fromstring(body)
    keys = [e.text or "" for e in doc.findall("s3:Contents/s3:Key", NS)]
    prefixes = [e.text or "" for e in doc.findall("s3:CommonPrefixes/s3:Prefix", NS)]
    return keys, prefixes, doc


def test_list_with_delimiter_groups_partitions(tmp_path: Path) -> None:
    """A delimiter listing returns one common prefix per partition directory."""

    root = _bucket(tmp_path)
    keys, prefixes, _ = _parse(
        list_objects(root, "s3", {"list-type": "2", "prefix": "raw/year=2026/", "delimiter": "/"})
    )
    assert keys == []
    assert prefixes == ["raw/year=2026/month=01/", "raw/year=2026/month=02/"]


def test_recursive_list_skips_hidden_files(tmp_path: Path) -> None:
    """A listing without delimiter returns every visible file with its size."""

    root = _bucket(tmp_path)
    keys, prefixes, doc = _parse(list_objects(root, "s3", {"list-type": "2"}))
    assert keys == [
        "_status.json",
        "raw/year=2026/month=01/part-00000.parquet",
        "raw/year=2026/month=02/part-00000.parquet",
    ]
    assert prefixes == []
    assert doc.findtext("s3:Contents/s3:Size", namespaces=NS) == "2"


def test_list_paginates_with_continuation_token(tmp_path: Path) -> None:
    """Truncated listings continue from the returned token."""

    root = _bucket(tmp_path)
    keys, _, doc = _parse(list_objects(root, "s3", {"list-type": "2", "max-keys": "2"}))
    assert len(keys) == 2
    assert doc.findtext("s3:IsTruncated", namespaces=NS) == "true"
    token = doc.findtext("s3:NextContinuationToken", namespaces=NS) or ""

    rest, _, doc = _parse(list_objects(root, "s3", {"list-type": "2", "continuation-token": token}))
    assert rest == ["raw/year=2026/month=02/part-00000.parquet"]
    assert doc.findtext("s3:IsTruncated", namespaces=NS) == "false"


@pytest.fixture
def server_url(tmp_path: Path):
    """Run the listing server on a free port for the duration of a test."""

    server = make_server(_bucket(tmp_path), "s3", "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_server_answers_bucket_listing(server_url: str) -> None:
    """The server answers path-style ListObjectsV2 requests on the bucket."""

    with urlopen(f"{server_url}/s3?list-type=2&prefix=raw/&delimiter=/") as response:
        _, prefixes, _ = _parse(response.read())
    assert prefixes == ["raw/year=2026/"]


def test_server_rejects_unknown_bucket(server_url: str) -> None:
    """Requests for another bucket get an S3 NoSuchBucket error."""

    with pytest.raises(HTTPError) as excinfo:
        urlopen(f"{server_url}/other?list-type=2")
    assert excinfo.value.code == 404
    assert b"NoSuchBucket" in excinfo.value.read()
