"""Container-only loopback HTTP endpoint carried over one exec's binary pipes.

This file is copied into the worker image and uses only the standard library.
The generated configuration path is an internal launcher argument, not an
operator configuration source. No upstream credential enters this process.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import queue
import struct
import sys
import threading
from typing import BinaryIO


HEADER = struct.Struct("!cI")
MAX_METADATA_BYTES = 16384
CHUNK_BYTES = 65536


def read_frame(stream: BinaryIO, limit: int) -> tuple[bytes, bytes]:
    def exact(size: int) -> bytes:
        result = bytearray()
        while len(result) < size:
            chunk = stream.read(size - len(result))
            if not chunk:
                raise EOFError("worker bridge input closed within a frame")
            result.extend(chunk)
        return bytes(result)

    kind, length = HEADER.unpack(exact(HEADER.size))
    if length > limit:
        raise ValueError(f"worker bridge frame {kind!r} exceeds {limit} bytes: {length}")
    return kind, exact(length)


def write_frame(stream: BinaryIO, kind: bytes, data: bytes) -> None:
    stream.write(HEADER.pack(kind, len(data)))
    stream.write(data)
    stream.flush()


class BridgeServer(HTTPServer):
    """One request at a time; Pi's next request cannot consume the prior body."""

    def __init__(self, port: int, max_request_bytes: int,
                 source: BinaryIO, destination: BinaryIO):
        super().__init__(("127.0.0.1", port), BridgeHandler)
        self.max_request_bytes = max_request_bytes
        self.destination = destination
        self.responses: queue.Queue[tuple[bytes, bytes] | Exception] = queue.Queue(maxsize=8)
        self.stopped = threading.Event()
        self.timeout = 0.25
        self.reader = threading.Thread(target=self._read, args=(source,), daemon=True)

    def _read(self, source: BinaryIO) -> None:
        try:
            while True:
                self.responses.put(read_frame(source, CHUNK_BYTES))
        except Exception as error:
            self.stopped.set()
            self.responses.put(error)

    def response(self) -> tuple[bytes, bytes]:
        result = self.responses.get()
        if isinstance(result, Exception):
            raise result
        return result

    def run(self) -> None:
        self.reader.start()
        write_frame(self.destination, b"R", json.dumps({"port": self.server_port}).encode())
        try:
            while not self.stopped.is_set():
                self.handle_request()
        finally:
            self.server_close()


class BridgeHandler(BaseHTTPRequestHandler):
    server: BridgeServer
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        # Only local request/body I/O uses this timeout; upstream waiting is on
        # the pipe, bounded by the host's configured model-call timeout.
        self.connection.settimeout(10)

    def _error(self, status: int, detail: str) -> None:
        body = json.dumps({"error": {"message": detail}}, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_POST(self) -> None:
        self.close_connection = True
        if self.path not in {"/v1/chat/completions", "/task/deliver-file"}:
            self._error(404, f"unsupported worker route: {self.path}")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if (self.headers.get("Transfer-Encoding") is not None or len(lengths) != 1
                or not lengths[0].isascii() or not lengths[0].isdecimal()):
            self._error(400, "worker request needs one Content-Length, without Transfer-Encoding")
            return
        length = int(lengths[0])
        if not 0 < length <= self.server.max_request_bytes:
            self._error(413, f"worker request exceeds {self.server.max_request_bytes} bytes or is empty")
            return
        authorizations = self.headers.get_all("Authorization", [])
        if len(authorizations) != 1 or not authorizations[0].startswith("Bearer "):
            self._error(401, "worker request needs one Bearer task token")
            return
        metadata = json.dumps({"token": authorizations[0][7:], "path": self.path,
                               "body_bytes": length}).encode()
        if len(metadata) > MAX_METADATA_BYTES:
            self._error(431, "worker authorization header is too large")
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self._error(400, f"worker body ended early: received {len(body)} of {length} bytes")
            return
        headers_sent = False
        disconnected = False
        response_ended = False
        try:
            write_frame(self.server.destination, b"Q", metadata)
            write_frame(self.server.destination, b"B", body)
            kind, data = self.server.response()
            if kind == b"X":
                detail = data.decode("utf-8")
                response_ended = True
                self._error(502, detail)
                return
            if kind != b"H":
                raise ValueError(f"expected worker response headers, got {kind!r}: {data[:500]!r}")
            metadata = json.loads(data)
            if (not isinstance(metadata, dict) or type(metadata.get("status")) is not int
                    or not 100 <= metadata["status"] <= 599
                    or not isinstance(metadata.get("headers"), dict)):
                raise ValueError(f"invalid worker response headers: {data[:500]!r}")
            headers_sent = True
            try:
                self.send_response(metadata["status"])
                for name, value in metadata["headers"].items():
                    if (name.lower() != "content-type" or not isinstance(value, str)
                            or "\r" in value or "\n" in value):
                        raise ValueError(f"unsupported worker response header: {name!r}={value!r}")
                    self.send_header(name, value)
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                self.end_headers()
            except (ConnectionError, TimeoutError):
                disconnected = True
            while True:
                kind, data = self.server.response()
                if kind == b"X":
                    detail = data.decode("utf-8")
                    response_ended = True
                    raise RuntimeError(detail)
                if kind == b"E":
                    if data:
                        raise ValueError("worker end frame must be empty")
                    response_ended = True
                    if not disconnected:
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                    return
                if kind != b"D" or not data:
                    raise ValueError(f"invalid worker body frame {kind!r}: {data[:500]!r}")
                if not disconnected:
                    try:
                        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                        self.wfile.flush()
                    except (ConnectionError, TimeoutError):
                        # Drain to E/X before accepting the next HTTP request.
                        disconnected = True
        except Exception as error:
            if not response_ended:
                # A malformed frame cannot leave queued bytes for a new call.
                self.server.stopped.set()
            if not headers_sent:
                self._error(502, f"{type(error).__name__}: {error}")
            else:
                # Do not append a valid chunk terminator to a truncated stream.
                self.log_error("%s: %s", type(error).__name__, error)


def main() -> None:
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if (not isinstance(config, dict) or set(config) != {"port", "max_request_bytes"}
            or type(config["port"]) is not int or not 0 <= config["port"] <= 65535
            or type(config["max_request_bytes"]) is not int or config["max_request_bytes"] <= 0):
        raise ValueError("invalid generated worker bridge configuration")
    BridgeServer(config["port"], config["max_request_bytes"],
                 sys.stdin.buffer, sys.stdout.buffer).run()


if __name__ == "__main__":
    main()
