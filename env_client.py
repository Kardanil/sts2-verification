"""Minimal TCP client for the sts2-env headless server."""

from __future__ import annotations

import json
import os
import socket
from typing import Any


class STS2Env:
    def __init__(self, host: str = "127.0.0.1", port: int = 9876, timeout: float | None = None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._reader = None
        self._debug = os.environ.get("STS2_VERIFICATION_DEBUG", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if self.timeout is not None:
            self._sock.settimeout(self.timeout)
        self._sock.connect((self.host, self.port))
        self._reader = self._sock.makefile("r")

    def _send_raw(self, msg: dict[str, Any]) -> dict[str, Any]:
        if self._sock is None:
            self.connect()
        if self._debug:
            print(f"    [tcp:{self.port}] -> {msg}", flush=True)
        assert self._sock is not None
        assert self._reader is not None
        self._sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
        line = self._reader.readline()
        if not line:
            raise ConnectionError("server closed connection")
        result = json.loads(line)
        if self._debug:
            keys = sorted(result.keys())
            print(f"    [tcp:{self.port}] <- {keys[:8]}", flush=True)
        return result

    def close(self) -> None:
        if self._reader is not None:
            try:
                self._reader.close()
            except OSError:
                pass
            self._reader = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
