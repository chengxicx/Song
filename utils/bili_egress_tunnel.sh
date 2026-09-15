#!/usr/bin/env bash
#
# Keep the Bilibili egress alive: the CONNECT proxy plus a self-healing
# SSH reverse tunnel to the Song/Lute server.
#
#     utils/bili_egress_tunnel.sh root@<server> [proxy-port] [tunnel-port]
#
# Leave it running in a terminal, or under launchd if you want it to come
# back after a reboot.  See lute/utils/outbound_proxy.py for why the
# server needs this at all.
#
# Why the loop: the tunnel is a single TCP connection, so it dies whenever
# this machine's IP changes, the network flaps, or the Mac wakes from
# sleep.  None of that needs configuration -- the next connection simply
# leaves from whatever address the machine has by then, and Bilibili sees
# that address.  Only the connection has to be re-established, which is
# what this script automates.
#
# The proxy and the tunnel are independent: the tunnel forwards each new
# request to 127.0.0.1:<proxy-port>, so restarting the proxy does not
# disturb an established tunnel, and vice versa.
set -u

SERVER="${1:-}"
PROXY_PORT="${2:-8888}"
TUNNEL_PORT="${3:-18888}"

if [ -z "$SERVER" ]; then
  echo "usage: $0 <user@server> [proxy-port] [tunnel-port]" >&2
  echo "  e.g. $0 root@203.0.113.10" >&2
  exit 2
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROXY="$REPO/utils/bili_egress_proxy.py"

if [ ! -f "$PROXY" ]; then
  echo "cannot find $PROXY" >&2
  exit 1
fi

# The proxy is stdlib-only, so whatever python3 is on PATH is enough.
PYTHON="$(command -v python3 || true)"
if [ -z "$PYTHON" ]; then
  echo "python3 not found on PATH" >&2
  exit 1
fi

# ---- the proxy, started once and left up -------------------------------
if pgrep -f "bili_egress_proxy.py $PROXY_PORT" >/dev/null 2>&1; then
  echo "proxy already running on 127.0.0.1:$PROXY_PORT"
else
  echo "starting proxy on 127.0.0.1:$PROXY_PORT"
  "$PYTHON" "$PROXY" "$PROXY_PORT" &
  PROXY_PID=$!
  sleep 1
  if ! kill -0 "$PROXY_PID" 2>/dev/null; then
    echo "proxy failed to start" >&2
    exit 1
  fi
fi

# ---- the tunnel, restarted whenever it drops ---------------------------
echo "tunnel: $SERVER  :$TUNNEL_PORT -> 127.0.0.1:$PROXY_PORT"
echo "press Ctrl-C to stop (the server then falls back to the embed player)"
while true; do
  ssh -N \
    -R "$TUNNEL_PORT:127.0.0.1:$PROXY_PORT" \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 \
    -o ConnectTimeout=15 \
    "$SERVER"
  echo "$(date '+%Y-%m-%d %H:%M:%S') tunnel dropped, reconnecting in 5s"
  sleep 5
done
