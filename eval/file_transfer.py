"""Measure file sharing through the network: upload a file to a room and download it again, print the throughput.

Run it from a client host (for example in the Mininet CLI: `h2 python3 eval/file_transfer.py --server 10.0.0.1 --mb 3`)
once with the SDN controller and once with CONTROLLER_MODE=baseline, ideally while a flood or iperf runs, and compare.
Files travel on port 5003, so the controller sends them through the bulk queue (500 kbit/s to 3 Mbit/s on the bottleneck).
"""
import argparse
import base64
import os
import socket
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from chat.protocol import recv_msg, send_msg  # noqa: E402
from common.constants import FILE_CHUNK, MAX_FILE, PORT_BULK, PORT_CONTROL, PORT_ROOM, SERVER_IP  # noqa: E402


def call(sock, type_, **fields):
    send_msg(sock, {"id": 1, "type": type_, **fields})
    r = recv_msg(sock)
    if r.get("type") == "ERR":
        raise SystemExit("server said %s %s" % (r.get("code"), r.get("msg", "")))
    return r


def login(server):
    name = "ft" + uuid.uuid4().hex[:8]
    ctl = socket.create_connection((server, PORT_CONTROL), timeout=10)
    token = call(ctl, "REGISTER", user=name, password="pw")["token"]
    bulk = socket.create_connection((server, PORT_BULK), timeout=60)
    call(bulk, "ATTACH", token=token)
    room = socket.create_connection((server, PORT_ROOM), timeout=10)     # needed so the room accepts us as a member
    call(room, "ATTACH", token=token)
    return name, ctl, bulk, room


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--server", default=SERVER_IP)
    ap.add_argument("--mb", type=float, default=2.0, help="file size in MB (max 5)")
    a = ap.parse_args()
    size = int(a.mb * 1024 * 1024)
    if not 0 < size <= MAX_FILE:
        raise SystemExit("size must be up to %d MB" % (MAX_FILE // (1024 * 1024)))
    data = os.urandom(size)
    _, ctl, bulk, _room = login(a.server)
    room = "ft" + uuid.uuid4().hex[:6]
    call(ctl, "JOIN", room=room)

    t0 = time.time()
    r = call(bulk, "FILE_UP", name="transfer.bin", size=size, room=room)
    parts = [data[i:i + FILE_CHUNK] for i in range(0, size, FILE_CHUNK)]
    for i, part in enumerate(parts):
        send_msg(bulk, {"id": 2, "type": "FILE_CHUNK", "fid": r["fid"], "seq": i,
                        "data": base64.b64encode(part).decode(), "last": i == len(parts) - 1})
    done = recv_msg(bulk)
    t_up = time.time() - t0
    if done.get("type") != "OK":
        raise SystemExit("upload failed: %s" % done)

    t1 = time.time()
    g = call(bulk, "FILE_GET", fid=r["fid"])
    got = b"".join(base64.b64decode(recv_msg(bulk)["data"]) for _ in range(g["chunks"]))
    t_down = time.time() - t1
    assert got == data, "downloaded file differs from the original"

    mbit = size * 8 / 1e6
    print("file %.2f MB  upload %.2f s (%.2f Mbit/s)  download %.2f s (%.2f Mbit/s)  intact: yes"
          % (size / 1048576, t_up, mbit / t_up, t_down, mbit / t_down))


if __name__ == "__main__":
    main()
