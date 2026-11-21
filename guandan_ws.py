"""guandan_ws.py —— 极简 WebSocket 客户端（wss 也支持），只依赖标准库。

为什么不用 `websocket-client`：这个项目是「exe 外壳 + 系统 Python」跑的，
多一个 pip 依赖就多一处新手卡住的地方。掼蛋只用文本帧，RFC 6455 的客户端
部分实现起来不到两百行，自己写更稳。

用法：
    ws = WebSocket("wss://gd-gs3.migufun.com:8444")
    ws.connect()
    ws.send_text("~m~36~m~xxxx")
    msg = ws.recv_text(timeout=5)      # 超时返回 None
    ws.close()
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import time

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BIN = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WSError(Exception):
    pass


def _parse_url(url: str):
    if "://" not in url:
        raise WSError(f"不是合法地址：{url}")
    scheme, rest = url.split("://", 1)
    scheme = scheme.lower()
    if scheme not in ("ws", "wss"):
        raise WSError(f"只支持 ws/wss：{url}")
    hostport, _, path = rest.partition("/")
    path = "/" + path
    if ":" in hostport:
        host, port = hostport.rsplit(":", 1)
        port = int(port)
    else:
        host = hostport
        port = 443 if scheme == "wss" else 80
    return scheme, host, port, path


class WebSocket(object):
    def __init__(self, url: str, headers=None, timeout: float = 20.0,
                 verify: bool = True):
        self.url = url
        self.extra_headers = dict(headers or {})
        self.timeout = timeout
        self.verify = verify
        self.sock = None
        self.buf = b""
        self.closed = False

    # ------------------------------------------------------------ 连接
    def connect(self):
        scheme, host, port, path = _parse_url(self.url)
        raw = socket.create_connection((host, port), timeout=self.timeout)
        if scheme == "wss":
            ctx = ssl.create_default_context()
            if not self.verify:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            raw = ctx.wrap_socket(raw, server_hostname=host)
        raw.settimeout(self.timeout)
        self.sock = raw

        key = base64.b64encode(os.urandom(16)).decode()
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}" + ("" if port in (80, 443) else f":{port}"),
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
            "Origin: https://gd.migufun.com",
            ("User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 26_5 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"),
        ]
        for k, v in self.extra_headers.items():
            lines.append(f"{k}: {v}")
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())

        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSError("握手时连接被关闭")
            head += chunk
        header, _, rest = head.partition(b"\r\n\r\n")
        self.buf = rest
        text = header.decode("latin-1")
        first = text.split("\r\n", 1)[0]
        if "101" not in first:
            raise WSError(f"握手失败：{first}")
        expect = base64.b64encode(
            hashlib.sha1((key + GUID).encode()).digest()).decode()
        for line in text.split("\r\n")[1:]:
            if line.lower().startswith("sec-websocket-accept:"):
                got = line.split(":", 1)[1].strip()
                if got != expect:
                    raise WSError("Sec-WebSocket-Accept 不匹配")
                break
        return self

    # ------------------------------------------------------------ 发送
    def send_text(self, text: str):
        if self.sock is None:
            raise WSError("还没连接")
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def _send_frame(self, opcode: int, payload: bytes):
        head = bytearray()
        head.append(0x80 | opcode)
        n = len(payload)
        # 客户端发出的帧必须加掩码
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack("!H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack("!Q", n)
        mask = os.urandom(4)
        head += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(head) + masked)

    # ------------------------------------------------------------ 接收
    def _read_exact(self, n: int, deadline: float) -> bytes:
        while len(self.buf) < n:
            left = deadline - time.time()
            if left <= 0:
                raise socket.timeout()
            self.sock.settimeout(max(0.05, min(left, 5.0)))
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            except OSError as exc:          # 包括 WinError 10054
                self.closed = True
                raise WSError(f"连接已断开：{exc}")
            if not chunk:
                self.closed = True
                raise WSError("连接已断开")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _read_frame(self, deadline: float):
        b1, b2 = self._read_exact(2, deadline)
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._read_exact(2, deadline))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._read_exact(8, deadline))[0]
        mask = self._read_exact(4, deadline) if masked else b""
        data = self._read_exact(n, deadline) if n else b""
        if masked and n:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return fin, opcode, data

    def recv_text(self, timeout=None):
        """读一条文本消息。超时返回 None；连接关闭抛 WSError。"""
        deadline = time.time() + (self.timeout if timeout is None else timeout)
        try:
            return self._recv_loop(deadline)
        except socket.timeout:
            return None

    def _recv_loop(self, deadline):
        chunks = []
        while True:
            fin, opcode, data = self._read_frame(deadline)
            if opcode == OP_PING:
                self._send_frame(OP_PONG, data)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                self.closed = True
                raise WSError("服务端要求关闭")
            if opcode in (OP_TEXT, OP_BIN, OP_CONT):
                chunks.append(data)
                if fin:
                    return b"".join(chunks).decode("utf-8", "replace")

    def close(self):
        if self.sock is None:
            return
        try:
            self._send_frame(OP_CLOSE, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
        self.sock = None
        self.closed = True
