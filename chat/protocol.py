"""Framing helpers shared by server and client. Part of the frozen contract."""
import json
import struct

from common.constants import MAX_FRAME


def send_msg(sock, obj):
    data = json.dumps(obj).encode("utf-8")
    if len(data) > MAX_FRAME:
        raise ValueError("frame too large")
    sock.sendall(struct.pack(">I", len(data)) + data)


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf += chunk
    return buf


def recv_msg(sock):
    """Return one decoded dict. Raises ConnectionError on close, ValueError on bad frame."""
    (n,) = struct.unpack(">I", _recv_exact(sock, 4))
    if n > MAX_FRAME:
        raise ValueError("frame too large")
    obj = json.loads(_recv_exact(sock, n).decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("frame is not an object")
    return obj
