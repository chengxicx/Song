"""
Make sure nginx serves /static/epub/ from the data directory.

Reader-mode EPUB chapters live under `<datapath>/static/epub/` and are
served at `/static/epub/...`.  nginx has a `location /static/` that
reads the code tree directly, which wins for any path below it unless a
more specific location exists -- and nginx picks the longest matching
prefix, so `/static/epub/` beats `/static/`.  Without that block the
chapters answer 404 while the Flask side looks perfectly healthy.

Run from deploy.sh, before the restart.  Idempotent; exits 0 and does
nothing when the block is already there or when this host has no nginx.
"""

import os
import shutil
import subprocess
import sys
import time

SITE = "/etc/nginx/sites-enabled/default"
# The comment line the block is inserted in front of.  If a server's
# nginx config does not have it, the file is left alone and the caller
# is told, rather than guessing at a placement.
MARKER = "# ---------- 静态资源"
ANCHOR = "location /static/epub/"
ALIAS = "alias /opt/lute/lute_data/static/epub/;"

BLOCK = """    # reader-mode EPUB 的章节与素材：在数据目录，与数据库同一份备份，
    # 不在代码目录里。前缀更长，nginx 会优先于下面的 /static/。
    location /static/epub/ {
        alias /opt/lute/lute_data/static/epub/;

        gzip on;
        gzip_types text/css application/javascript text/plain image/svg+xml;
        gzip_min_length 256;
        gzip_comp_level 6;

        expires 1y;
        add_header Cache-Control "public, immutable";
        log_not_found off;
    }

"""


def reload_nginx():
    subprocess.run(["nginx", "-t"], check=True, capture_output=True)
    subprocess.run(["systemctl", "reload", "nginx"], check=True, capture_output=True)


def main():
    if shutil.which("nginx") is None or not os.path.isfile(SITE):
        print("no nginx site config here -- nothing to do")
        return 0

    with open(SITE, encoding="utf-8") as f:
        source = f.read()

    if ANCHOR in source:
        if ALIAS not in source:
            print(
                "⚠️ nginx already has /static/epub/ but not the data-directory "
                "alias -- check " + SITE,
                file=sys.stderr,
            )
            return 1
        print("nginx /static/epub/ already points at the data directory")
        return 0

    index = source.find(MARKER)
    if index == -1:
        print(
            "⚠️ nginx config has no static-resources section; add a "
            "location /static/epub/ block by hand (" + SITE + ")",
            file=sys.stderr,
        )
        return 1

    # Keep a copy before touching a file the whole site depends on.
    backup = "%s.bak_%s" % (SITE, time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(SITE, backup)

    with open(SITE, "w", encoding="utf-8") as f:
        f.write(source[:index] + BLOCK + source[index:])
    print("nginx: added /static/epub/ -> data directory")

    try:
        reload_nginx()
    except subprocess.CalledProcessError as exc:
        shutil.copy2(backup, SITE)
        print("⚠️ nginx rejected the change, reverted: %s" % exc, file=sys.stderr)
        return 1
    print("nginx reloaded (%s)" % backup)
    return 0


if __name__ == "__main__":
    sys.exit(main())
