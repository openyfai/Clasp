"""
Egress proxy for worker sandboxes.

Enforces per-lease domain allowlists loaded from worker workspace policy files.
Default-deny when no domains are configured.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger("agent.security.network_proxy")

DEFAULT_ALLOWED_DOMAINS: set[str] = set()
from clasp.vendor.silex.utils.config import KRONOS_WORKSPACE
_POLICY_ROOT = KRONOS_WORKSPACE
_PROXY_PORT = 8080


def _sanitize_hostname(host: str) -> Optional[str]:
    if not host or "\x00" in host:
        return None
    host = host.split(":")[0].strip().lower()
    if not host or len(host) > 253:
        return None
    return host


def _load_policy_for_worker(worker_id: str) -> tuple[bool, set[str]]:
    """Load egress policy from worker workspace directory."""
    policy_file = _POLICY_ROOT / worker_id / ".egress_policy.json"
    if not policy_file.exists():
        return False, set()
    try:
        data = json.loads(policy_file.read_text(encoding="utf-8"))
        network_allowed = bool(data.get("network_allowed", False))
        domains = {d.lower() for d in data.get("allowed_domains", []) if d}
        return network_allowed, domains
    except (json.JSONDecodeError, OSError):
        return False, set()


def _host_allowed(host: str, worker_id: str) -> bool:
    host = _sanitize_hostname(host)
    if host is None:
        return False

    network_allowed, domains = _load_policy_for_worker(worker_id)
    if not network_allowed:
        return False

    effective = domains if domains else DEFAULT_ALLOWED_DOMAINS
    if not effective:
        return False

    for domain in effective:
        if host == domain or host.endswith("." + domain):
            return True
    return False


class _ProxyHandler(BaseHTTPRequestHandler):
    worker_id: str = ""

    def log_message(self, fmt: str, *args) -> None:
        log.debug("proxy[%s]: " + fmt, self.worker_id, *args)

    def do_CONNECT(self) -> None:
        host_port = self.path
        host = host_port.split(":")[0] if ":" in host_port else host_port
        if not _host_allowed(host, self.worker_id):
            self.send_error(403, "Egress denied by lease policy")
            return
        try:
            port = int(host_port.split(":")[1]) if ":" in host_port else 443
            remote = socket.create_connection((host, port), timeout=10)
            self.send_response(200, "Connection Established")
            self.end_headers()
            self._tunnel(self.connection, remote)
        except OSError as exc:
            self.send_error(502, str(exc))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        host = parsed.hostname or self.headers.get("Host", "").split(":")[0]
        if not _host_allowed(host, self.worker_id):
            self.send_error(403, "Egress denied by lease policy")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Kronos egress proxy active\n")

    def _tunnel(self, client: socket.socket, remote: socket.socket) -> None:
        import select

        sockets = [client, remote]
        try:
            while True:
                readable, _, _ = select.select(sockets, [], [], 30)
                if not readable:
                    break
                for sock in readable:
                    data = sock.recv(8192)
                    if not data:
                        return
                    other = remote if sock is client else client
                    other.sendall(data)
        finally:
            remote.close()


class EgressProxyServer:
    """HTTP CONNECT egress proxy for worker sandboxes."""

    def __init__(self, worker_id: str = "shared", port: int = _PROXY_PORT):
        self.worker_id = worker_id
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port = port

    def start(self) -> int:
        handler = type("Handler", (_ProxyHandler,), {"worker_id": self.worker_id})
        self._server = HTTPServer(("0.0.0.0", self.port), handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        log.info("Egress proxy listening on 0.0.0.0:%d", self.port)
        return self.port

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    server = EgressProxyServer()
    server.start()
    try:
        while True:
            threading.Event().wait(3600)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
