"""Answer S3 bucket listing requests for a local directory.

Together with a static file server for object ``GET``/``HEAD`` requests this
makes the dataset readable as an anonymous, read-only S3 bucket, so clients
such as pandas, pyarrow and DuckDB can discover Hive partitions themselves.
Only bucket-level requests (``ListObjects`` v1/v2, ``GetBucketLocation`` and
``HeadBucket``) are handled here. Request signatures are not checked.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

S3_NAMESPACE = "http://s3.amazonaws.com/doc/2006-03-01/"
MAX_KEYS = 1000


def _iter_objects(root: Path) -> list[tuple[str, Path]]:
    """Return ``(key, path)`` for every visible file below ``root``.

    Parameters
    ----------
    root
        Directory exposed as the bucket.

    Returns
    -------
    list[tuple[str, pathlib.Path]]
        Object keys (POSIX relative paths) and their files. Hidden files and
        directories, such as in-progress ``.tmp`` writes, are skipped.
    """

    objects = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if path.is_file() and not any(part.startswith(".") for part in relative.parts):
            objects.append((relative.as_posix(), path))
    return objects


def _etag(path: Path) -> str:
    """Return an nginx-compatible ETag for a file.

    Parameters
    ----------
    path
        File path.

    Returns
    -------
    str
        Quoted ``"<mtime hex>-<size hex>"`` string, matching nginx's static ETag.
    """

    stat = path.stat()
    return f'"{int(stat.st_mtime):x}-{stat.st_size:x}"'


def _sub(parent: ET.Element, tag: str, text: object) -> ET.Element:
    """Append a namespaced child element with text.

    Parameters
    ----------
    parent
        Parent element.
    tag
        Tag name without namespace.
    text
        Element text; booleans are lower-cased.

    Returns
    -------
    xml.etree.ElementTree.Element
        The new element.
    """

    element = ET.SubElement(parent, tag)
    element.text = str(text).lower() if isinstance(text, bool) else str(text)
    return element


def list_objects(root: str | Path, bucket: str, params: dict[str, str]) -> bytes:
    """Build a ``ListObjects`` (v1) or ``ListObjectsV2`` XML response.

    Parameters
    ----------
    root
        Directory exposed as the bucket.
    bucket
        Bucket name echoed in the response.
    params
        Query parameters: ``list-type``, ``prefix``, ``delimiter``,
        ``max-keys``, ``continuation-token``, ``start-after`` and ``marker``.

    Returns
    -------
    bytes
        UTF-8 encoded XML document.
    """

    root = Path(root)
    v2 = params.get("list-type") == "2"
    prefix = params.get("prefix", "")
    delimiter = params.get("delimiter", "")
    max_keys = max(0, min(int(params.get("max-keys", MAX_KEYS)), MAX_KEYS))
    if v2:
        after = params.get("continuation-token") or params.get("start-after", "")
    else:
        after = params.get("marker", "")

    entries: dict[str, Path | None] = {}
    for key, path in _iter_objects(root):
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix) :]
        if delimiter and delimiter in rest:
            common_prefix = prefix + rest[: rest.index(delimiter) + len(delimiter)]
            entries[common_prefix] = None
        else:
            entries[key] = path
    remaining = sorted(key for key in entries if key > after)
    page = remaining[:max_keys]
    truncated = len(remaining) > len(page)

    ET.register_namespace("", S3_NAMESPACE)
    result = ET.Element(f"{{{S3_NAMESPACE}}}ListBucketResult")
    _sub(result, "Name", bucket)
    _sub(result, "Prefix", prefix)
    if delimiter:
        _sub(result, "Delimiter", delimiter)
    _sub(result, "MaxKeys", max_keys)
    _sub(result, "IsTruncated", truncated)
    if v2:
        _sub(result, "KeyCount", len(page))
        if params.get("continuation-token"):
            _sub(result, "ContinuationToken", params["continuation-token"])
        if params.get("start-after"):
            _sub(result, "StartAfter", params["start-after"])
        if truncated:
            _sub(result, "NextContinuationToken", page[-1])
    else:
        _sub(result, "Marker", after)
        if truncated:
            _sub(result, "NextMarker", page[-1])

    for key in page:
        path = entries[key]
        if path is None:
            common = ET.SubElement(result, "CommonPrefixes")
            _sub(common, "Prefix", key)
            continue
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        contents = ET.SubElement(result, "Contents")
        _sub(contents, "Key", key)
        _sub(contents, "LastModified", modified.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        _sub(contents, "ETag", _etag(path))
        _sub(contents, "Size", stat.st_size)
        _sub(contents, "StorageClass", "STANDARD")

    return ET.tostring(result, encoding="utf-8", xml_declaration=True)


def _error(code: str, message: str) -> bytes:
    """Build an S3 error XML document.

    Parameters
    ----------
    code
        S3 error code, e.g. ``NoSuchBucket``.
    message
        Human-readable message.

    Returns
    -------
    bytes
        UTF-8 encoded XML document.
    """

    error = ET.Element("Error")
    _sub(error, "Code", code)
    _sub(error, "Message", message)
    return ET.tostring(error, encoding="utf-8", xml_declaration=True)


def make_server(root: str | Path, bucket: str, host: str, port: int) -> ThreadingHTTPServer:
    """Create an HTTP server answering bucket-level S3 requests for ``root``.

    Parameters
    ----------
    root
        Directory exposed as the bucket.
    bucket
        Bucket name clients use in path-style requests (``/<bucket>?list-type=2``).
    host
        Interface to bind.
    port
        Port to bind; ``0`` picks a free port.

    Returns
    -------
    http.server.ThreadingHTTPServer
        Server ready for ``serve_forever()``.
    """

    root = Path(root)

    class Handler(BaseHTTPRequestHandler):
        """Handle ``GET``/``HEAD`` on the bucket path."""

        def _send(self, status: int, body: bytes = b"") -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self) -> None:  # noqa: N802
            if urlsplit(self.path).path.strip("/") != bucket:
                self._send(404)
                return
            self._send(200)

        def do_GET(self) -> None:  # noqa: N802
            url = urlsplit(self.path)
            if url.path.strip("/") != bucket:
                self._send(404, _error("NoSuchBucket", "The specified bucket does not exist"))
                return
            query = parse_qs(url.query, keep_blank_values=True)
            if "location" in query:
                constraint = ET.Element(f"{{{S3_NAMESPACE}}}LocationConstraint")
                self._send(200, ET.tostring(constraint, encoding="utf-8", xml_declaration=True))
                return
            params = {name: values[0] for name, values in query.items()}
            try:
                body = list_objects(root, bucket, params)
            except ValueError as exc:
                self._send(400, _error("InvalidArgument", str(exc)))
                return
            self._send(200, body)

    return ThreadingHTTPServer((host, port), Handler)
