from __future__ import annotations

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"


@dataclass
class JsonRpcMessage:
    method: str | None = None
    params: dict[str, Any] | None = None
    result: Any = None
    error: dict[str, Any] | None = None
    id: int | str | None = None
    jsonrpc: str = JSONRPC_VERSION

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc}
        if self.method is not None:
            d["method"] = self.method
        if self.params is not None:
            d["params"] = self.params
        if self.result is not None:
            d["result"] = self.result
        if self.error is not None:
            d["error"] = self.error
        if self.id is not None:
            d["id"] = self.id
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JsonRpcMessage:
        return cls(
            method=d.get("method"),
            params=d.get("params"),
            result=d.get("result"),
            error=d.get("error"),
            id=d.get("id"),
            jsonrpc=d.get("jsonrpc", JSONRPC_VERSION),
        )


class McpTransport(ABC):
    @abstractmethod
    async def start(self) -> None:
        ...

    @abstractmethod
    async def send(self, message: JsonRpcMessage) -> None:
        ...

    @abstractmethod
    async def receive(self) -> JsonRpcMessage | None:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        ...


class StdioTransport(McpTransport):
    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._command = command
        self._args = args or []
        self._env = env
        self._process: asyncio.subprocess.Process | None = None
        self._read_buffer = b""
        self._stderr_output = ""
        self._closed = False

    async def start(self) -> None:
        merged_env = {**os.environ}
        if self._env:
            merged_env.update(self._env)

        self._process = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged_env,
        )
        if self._process.stderr:
            asyncio.get_event_loop().create_task(self._read_stderr())

    async def _read_stderr(self) -> None:
        if self._process is None or self._process.stderr is None:
            return
        try:
            while True:
                data = await self._process.stderr.read(4096)
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                if len(self._stderr_output) < 64 * 1024 * 1024:
                    self._stderr_output += text
        except Exception:
            pass

    async def send(self, message: JsonRpcMessage) -> None:
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("Transport not started")
        data = json.dumps(message.to_dict())
        content = data.encode("utf-8")
        header = f"Content-Length: {len(content)}\r\n\r\n".encode("utf-8")
        self._process.stdin.write(header + content)
        await self._process.stdin.drain()

    async def receive(self) -> JsonRpcMessage | None:
        if self._process is None or self._process.stdout is None:
            return None

        try:
            content_length = await self._read_header()
            if content_length is None:
                return None
            body = await self._read_exactly(content_length)
            if body is None:
                return None
            data = json.loads(body.decode("utf-8"))
            return JsonRpcMessage.from_dict(data)
        except (json.JSONDecodeError, asyncio.IncompleteReadError, ConnectionError):
            return None

    async def _read_header(self) -> int | None:
        if self._process is None or self._process.stdout is None:
            return None
        while True:
            sep_idx = self._read_buffer.find(b"\r\n\r\n")
            if sep_idx != -1:
                header_data = self._read_buffer[:sep_idx]
                self._read_buffer = self._read_buffer[sep_idx + 4:]
                for line in header_data.decode("utf-8").split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        try:
                            return int(line.split(":", 1)[1].strip())
                        except (ValueError, IndexError):
                            return None
                return None
            chunk = await self._process.stdout.read(4096)
            if not chunk:
                return None
            self._read_buffer += chunk

    async def _read_exactly(self, n: int) -> bytes | None:
        if self._process is None or self._process.stdout is None:
            return None
        while len(self._read_buffer) < n:
            chunk = await self._process.stdout.read(max(4096, n - len(self._read_buffer)))
            if not chunk:
                return None
            self._read_buffer += chunk
        data = self._read_buffer[:n]
        self._read_buffer = self._read_buffer[n:]
        return data

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process is not None:
            if self._process.stdin:
                try:
                    self._process.stdin.close()
                except Exception:
                    pass
            try:
                self._process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass

    @property
    def is_connected(self) -> bool:
        return (
            not self._closed
            and self._process is not None
            and self._process.returncode is None
        )

    @property
    def stderr_output(self) -> str:
        return self._stderr_output


class HttpTransport(McpTransport):
    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self._url = url
        self._headers = headers or {}
        self._connected = False

    async def start(self) -> None:
        self._connected = True

    async def send(self, message: JsonRpcMessage) -> None:
        raise NotImplementedError("HTTP transport not yet implemented")

    async def receive(self) -> JsonRpcMessage | None:
        raise NotImplementedError("HTTP transport not yet implemented")

    async def close(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected


class SseTransport(McpTransport):
    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self._url = url
        self._headers = headers or {}
        self._connected = False

    async def start(self) -> None:
        self._connected = True

    async def send(self, message: JsonRpcMessage) -> None:
        raise NotImplementedError("SSE transport not yet implemented")

    async def receive(self) -> JsonRpcMessage | None:
        raise NotImplementedError("SSE transport not yet implemented")

    async def close(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected
