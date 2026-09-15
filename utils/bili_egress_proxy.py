#!/usr/bin/env python3
"""
Expose this machine as a Bilibili egress for the Song/Lute server.

Bilibili bans the production server's IP (see
``lute/utils/outbound_proxy.py`` for the details and the evidence), so the
server has to reach Bilibili through an IP Bilibili accepts.  This script
is the far end of that arrangement: a tiny HTTP CONNECT proxy you run on a
machine with an accepted IP, typically a home Mac in mainland China.

Two steps, both on the machine with the accepted IP:

    # 1. the proxy
    python3 utils/bili_egress_proxy.py 8888

    # 2. a reverse tunnel so the server can reach it (SSH is enough --
    #    no public IP, no port forwarding, nothing to buy)
    ssh -N -R 18888:127.0.0.1:8888 root@<server>

Then on the server:

    LUTE_BILIBILI_PROXY=http://127.0.0.1:18888

Verify from the server with:

    python3 -c "import os,requests; \\
      r=requests.get('https://api.bilibili.com/x/web-interface/view?bvid=BV1aa411J7dB', \\
      headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.bilibili.com'}, \\
      proxies={'https':'http://127.0.0.1:18888'}, timeout=20); \\
      print(r.status_code, r.json()['code'])"

A healthy setup prints ``200 0``.  ``412`` means the egress IP is not one
Bilibili accepts either.

Notes
-----
* Only CONNECT (HTTPS) is implemented, which is all Bilibili needs, so a
  caller cannot abuse this as a general plaintext relay.
* The listener binds loopback only.  ``ssh -R`` without ``GatewayPorts``
  exposes it on the server's loopback too, so it is never reachable from
  the internet.
* The tunnel dies if this script stops, this machine sleeps, or the SSH
  session drops; the server then falls back to the embed player.
"""

import select
import socket
import sys
import threading

IDLE_TIMEOUT = 120
BUFSIZE = 65536
CONNECT_TIMEOUT = 15


def _pump(a, b):
    "Copy bytes both ways until either side closes or goes idle."
    try:
        while True:
            readable, _, _ = select.select([a, b], [], [], IDLE_TIMEOUT)
            if not readable:
                return
            for src in readable:
                data = src.recv(BUFSIZE)
                if not data:
                    return
                (b if src is a else a).sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


def _handle(conn):
    "Serve one CONNECT request."
    try:
        conn.settimeout(30)
        request = b""
        while b"\r\n\r\n" not in request and len(request) < 65536:
            chunk = conn.recv(4096)
            if not chunk:
                return
            request += chunk
        request_line = request.split(b"\r\n", 1)[0].decode("latin-1")
        parts = request_line.split()
        if len(parts) != 3 or parts[0].upper() != "CONNECT":
            conn.sendall(b"HTTP/1.1 501 Not Implemented\r\n\r\n")
            return
        host, _, port = parts[1].rpartition(":")
        if not host or not port.isdigit():
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            return
        upstream = socket.create_connection((host, int(port)), timeout=CONNECT_TIMEOUT)
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        conn.settimeout(None)
        _pump(conn, upstream)
    except OSError:
        try:
            conn.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        except OSError:
            pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def serve(port):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(64)
    print(f"CONNECT proxy listening on 127.0.0.1:{port}", flush=True)
    print(
        "Next: ssh -N -R 18888:127.0.0.1:%d root@<server>" % port,
        flush=True,
    )
    while True:
        conn, _addr = server.accept()
        threading.Thread(target=_handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8888)
