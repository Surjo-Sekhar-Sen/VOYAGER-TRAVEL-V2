"""Minimal Chrome DevTools Protocol client (no external deps).

Speaks to a headless Chrome over its debug WebSocket so the app can be driven
the way a user drives it: type in the search box, click cards, switch tabs.
"""
import base64
import json
import os
import socket
import struct
import time
import urllib.request


class WS:
    def __init__(self, url: str):
        # ws://host:port/path
        rest = url.split("://", 1)[1]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        if b"101" not in buf.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"handshake failed: {buf[:200]!r}")
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self._id = 0
        self.events = []

    def _recv_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv_frame(self):
        b1, b2 = self._recv_exact(2)
        opcode = b1 & 0x0F
        masked = b2 & 0x80
        ln = b2 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._recv_exact(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else None
        payload = self._recv_exact(ln) if ln else b""
        if mask:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        return opcode, payload

    def send(self, text: str):
        data = text.encode()
        header = bytearray([0x81])
        ln = len(data)
        if ln < 126:
            header.append(0x80 | ln)
        elif ln < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", ln)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", ln)
        mask = os.urandom(4)
        header += mask
        masked = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def call(self, method: str, params: dict | None = None, timeout=60):
        self._id += 1
        mid = self._id
        self.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            opcode, payload = self.recv_frame()
            if opcode == 0x9:  # ping
                continue
            if opcode != 0x1:
                continue
            msg = json.loads(payload)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result")
            if "method" in msg:
                self.events.append(msg)
        raise TimeoutError(f"no reply for {method}")


def page_ws(port: int, url_contains: str = "localhost:3000") -> str:
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
                for t in json.load(r):
                    if t.get("type") == "page" and url_contains in (t.get("url") or ""):
                        return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("no page target found")


def click_js(selector: str, index: int = 0) -> str:
    """Click helper that also works for SVG nodes (no .click() method there)."""
    return f"""
    (() => {{
      const els = document.querySelectorAll({json.dumps(selector)});
      const el = els[{index}];
      if (!el) return 'NOT_FOUND';
      el.scrollIntoView({{ block: 'center' }});
      if (typeof el.click === 'function') {{ el.click(); }}
      else {{ el.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, view: window }})); }}
      return (el.getAttribute && (el.getAttribute('class') || el.tagName)) || el.tagName;
    }})()
    """


def evaluate(ws: WS, expr: str, timeout=60):
    res = ws.call("Runtime.evaluate", {
        "expression": expr,
        "awaitPromise": True,
        "returnByValue": True,
    }, timeout=timeout)
    if res.get("exceptionDetails"):
        raise RuntimeError(json.dumps(res["exceptionDetails"])[:600])
    return res["result"].get("value")


def wait_for(ws: WS, expr: str, timeout=30, interval=0.4, label=""):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = evaluate(ws, expr)
        if last:
            return last
        time.sleep(interval)
    raise TimeoutError(f"condition never true: {label or expr} (last={last!r})")
