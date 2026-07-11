"""Allowlisting egress proxy for the Docker sandbox.

Runs as a sidecar container attached to both the internal sandbox network and
the default bridge. Task containers sit on the internal network (no external
route), so this proxy is their only path out — HTTP(S) egress is therefore
limited to ALLOWED_HOSTS.

Supports CONNECT tunnels (HTTPS) and plain absolute-URI HTTP requests, which
covers package managers (pip/npm) and git-over-https.

    ALLOWED_HOSTS="pypi.org,*.pypi.org,files.pythonhosted.org" python egress_proxy.py

Stdlib only, so it can run in a bare python:*-slim image.
"""

from __future__ import annotations

import os
import select
import socket
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PROXY_PORT", "8888"))


def parse_allowlist(raw: str) -> list[str]:
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def host_allowed(host: str, allowed: list[str]) -> bool:
    """Exact match, or "*.example.com" matching the domain and any subdomain."""
    host = host.lower().rstrip(".")
    for pattern in allowed:
        if pattern == host:
            return True
        if pattern.startswith("*."):
            root = pattern[2:]
            if host == root or host.endswith("." + root):
                return True
    return False


ALLOWED = parse_allowlist(os.environ.get("ALLOWED_HOSTS", ""))


def _tunnel(client: socket.socket, upstream: socket.socket) -> None:
    sockets = [client, upstream]
    while True:
        readable, _, errored = select.select(sockets, [], sockets, 60)
        if errored or not readable:
            break
        for sock in readable:
            data = sock.recv(65536)
            if not data:
                return
            (upstream if sock is client else client).sendall(data)


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    def do_CONNECT(self):
        host, _, port = self.path.partition(":")
        if not host_allowed(host, ALLOWED):
            self.send_error(403, f"egress to {host} not in allowlist")
            return
        try:
            upstream = socket.create_connection((host, int(port or 443)), timeout=30)
        except OSError as exc:
            self.send_error(502, f"cannot reach {host}: {exc}")
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        try:
            _tunnel(self.connection, upstream)
        finally:
            upstream.close()

    def _forward(self):
        from urllib.parse import urlsplit

        host = urlsplit(self.path).hostname or ""
        if not host_allowed(host, ALLOWED):
            self.send_error(403, f"egress to {host} not in allowlist")
            return
        body = None
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            body = self.rfile.read(length)
        request = urllib.request.Request(self.path, data=body, method=self.command)
        for header in ("User-Agent", "Accept", "Content-Type", "Authorization"):
            if self.headers.get(header):
                request.add_header(header, self.headers[header])
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
                self.send_response(response.status)
                for key, value in response.headers.items():
                    if key.lower() in ("content-type", "content-length"):
                        self.send_header(key, value)
                if "Content-Length" not in response.headers:
                    self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except Exception as exc:  # noqa: BLE001 - proxy edge, report upstream failure
            self.send_error(502, f"upstream error: {exc}")

    do_GET = do_HEAD = do_POST = do_PUT = _forward


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), ProxyHandler)
    print(f"egress proxy on :{PORT}, allowlist: {ALLOWED or '(empty — everything blocked)'}")
    server.serve_forever()


if __name__ == "__main__":
    main()
