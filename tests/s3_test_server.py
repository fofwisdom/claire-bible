"""A local S3-compatible server for tests (FEDERATION_REMOTE_READING_DESIGN.md Section 14.2: the object storage tests use a local S3-compatible server).

The server keeps objects in memory. It has the four operations that Claire uses (Section 9.1.1 rule 4): ``PutObject``, ``GetObject`` with ``Range``, ``HeadObject``, and ``DeleteObject``, with path-style addressing. It checks AWS Signature Version 4 with the raw request path, as a real server does, and the header ``x-amz-content-sha256`` against the body. A request with a wrong signature gets 403.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ACCESS_KEY = "GKtestaccesskey0000000000"
SECRET_KEY = "test-secret-key-0123456789abcdef0123456789abcdef"
REGION = "garage"
BUCKET = "claire-media"


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


class LocalS3Server:
    """Start with ``start()``; ``endpoint`` is the address. ``objects`` maps ``bucket/key`` to ``(bytes, content type)``. Set ``fail`` to an HTTP status to make all requests fail."""

    def __init__(self, *, bucket: str = BUCKET, access_key: str = ACCESS_KEY, secret_key: str = SECRET_KEY, region: str = REGION) -> None:
        self.bucket, self.access_key, self.secret_key, self.region = bucket, access_key, secret_key, region
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.requests: list[tuple[str, str]] = []
        self.fail: int | None = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:
                pass

            def _answer(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None, *, head: bool = False) -> None:
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                if "Content-Length" not in (headers or {}):
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head:
                    self.wfile.write(body)

            def _signature_ok(self, body: bytes) -> bool:
                auth = self.headers.get("authorization", "")
                if not auth.startswith("AWS4-HMAC-SHA256 "):
                    return False
                fields = dict(part.strip().split("=", 1) for part in auth[len("AWS4-HMAC-SHA256 ") :].split(","))
                credential = fields.get("Credential", "").split("/")
                if len(credential) != 5 or credential[0] != owner.access_key or credential[2] != owner.region or credential[3] != "s3":
                    return False
                payload_hash = self.headers.get("x-amz-content-sha256", "")
                if payload_hash != hashlib.sha256(body).hexdigest():
                    return False
                signed = fields.get("SignedHeaders", "").split(";")
                if "host" not in signed or "x-amz-date" not in signed:
                    return False
                path, _, query = self.path.partition("?")
                canonical_headers = "".join(f"{name}:{(self.headers.get(name) or '').strip()}\n" for name in signed)
                canonical = f"{self.command}\n{path}\n{query}\n{canonical_headers}\n{';'.join(signed)}\n{payload_hash}"
                amz_date = self.headers.get("x-amz-date", "")
                scope = f"{credential[1]}/{owner.region}/s3/aws4_request"
                to_sign = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
                key = _sign(_sign(_sign(_sign(f"AWS4{owner.secret_key}".encode(), credential[1]), owner.region), "s3"), "aws4_request")
                return hmac.compare_digest(hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest(), fields.get("Signature", ""))

            def _handle(self) -> None:
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                head = self.command == "HEAD"
                owner.requests.append((self.command, self.path))
                if owner.fail is not None:
                    return self._answer(owner.fail, b"<Error/>", head=head)
                if not self._signature_ok(body):
                    return self._answer(403, b"<Error><Code>SignatureDoesNotMatch</Code></Error>", head=head)
                bucket, _, key = self.path.partition("?")[0].lstrip("/").partition("/")
                if bucket != owner.bucket:
                    return self._answer(404, b"<Error><Code>NoSuchBucket</Code></Error>", head=head)
                name = f"{bucket}/{key}"
                if self.command == "PUT":
                    owner.objects[name] = (body, self.headers.get("content-type", "application/octet-stream"))
                    return self._answer(200, headers={"ETag": f'"{hashlib.md5(body).hexdigest()}"'})
                if self.command == "DELETE":
                    owner.objects.pop(name, None)
                    return self._answer(204)
                if name not in owner.objects:
                    return self._answer(404, b"<Error><Code>NoSuchKey</Code></Error>", head=head)
                data, content_type = owner.objects[name]
                headers = {"Content-Type": content_type, "ETag": f'"{hashlib.md5(data).hexdigest()}"', "Accept-Ranges": "bytes"}
                if head:
                    return self._answer(200, headers={**headers, "Content-Length": str(len(data))}, head=True)
                range_header = self.headers.get("range")
                if range_header and range_header.startswith("bytes="):
                    start_text, _, end_text = range_header[len("bytes=") :].partition("-")
                    start = int(start_text)
                    end = min(int(end_text) if end_text else len(data) - 1, len(data) - 1)
                    if start >= len(data):
                        return self._answer(416)
                    return self._answer(206, data[start : end + 1], {**headers, "Content-Range": f"bytes {start}-{end}/{len(data)}"})
                return self._answer(200, data, headers)

            do_GET = do_PUT = do_HEAD = do_DELETE = _handle

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> "LocalS3Server":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
