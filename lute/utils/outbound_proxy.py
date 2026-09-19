"""
Optional outbound proxy for hosts the server cannot reach directly.

Bilibili refuses the server's own IP outright.  Its API answers HTTP 412
(``{"code": -412, "message": "request was banned"}``) to datacenter and
overseas addresses no matter which headers or cookies are sent, and its
CDN then returns HTTP 959 or simply times out.  Headers and cookies do
not help -- the ban is on the IP -- so the only fix is to reach Bilibili
through an egress it accepts.

The usual setup is a home machine in mainland China, reached with an SSH
reverse tunnel (no public IP or extra hosting needed):

    # on the machine whose IP Bilibili accepts
    python3 utils/bili_egress_proxy.py 8888
    ssh -N -R 18888:127.0.0.1:8888 root@<server>

    # on the server (systemd Environment=, or the shell for a test)
    LUTE_BILIBILI_PROXY=http://127.0.0.1:18888

Unset (the default) means requests go out directly, exactly as before,
so this only ever changes behaviour when explicitly configured.
"""

import os

BILIBILI_PROXY_ENV = "LUTE_BILIBILI_PROXY"


def bilibili_proxies():
    """
    A ``requests`` proxies dict for Bilibili, or None to call directly.

    Returns None unless LUTE_BILIBILI_PROXY is set to something non-empty,
    so callers can pass the result straight to ``requests`` without
    branching.
    """
    url = (os.environ.get(BILIBILI_PROXY_ENV) or "").strip()
    if not url:
        return None
    return {"http": url, "https": url}


def describe_bilibili_proxy():
    "One-line description of the proxy setting, for logs and diagnostics."
    url = (os.environ.get(BILIBILI_PROXY_ENV) or "").strip()
    if not url:
        return f"{BILIBILI_PROXY_ENV} is not set (Bilibili requests go out directly)"
    return f"{BILIBILI_PROXY_ENV} = {url}"
