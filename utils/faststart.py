"""Move an MP4/M4A 'moov' atom to the front of the file (faststart).

Command-line front end for lute/utils/mp4faststart.py -- the same
implementation the app runs on every uploaded or downloaded media file
(no ffmpeg, no re-encode, lossless).  This script loads that module
directly by path, so it runs under any Python 3 with no install.

Usage:
  python3 utils/faststart.py <file> [--apply] [--backup]

Without --apply it only reports the atom layout and what it would do.
"""
import importlib.util
import os
import shutil
import sys


def _load_module():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, os.pardir, "lute", "utils", "mp4faststart.py")
    spec = importlib.util.spec_from_file_location("mp4faststart", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply_ = "--apply" in sys.argv
    backup = "--backup" in sys.argv
    if not args:
        print(__doc__)
        return 1
    path = args[0]
    mp4faststart = _load_module()

    print("top-level atoms:")
    for typ, start, size, _hdr in mp4faststart.top_level_boxes(path):
        print(f"  {typ.decode('latin1', 'replace'):<6} size={size:>10} offset={start}")

    typ_list = [typ for typ, _s, _z, _h in mp4faststart.top_level_boxes(path)]
    if "moov" not in typ_list or "mdat" not in typ_list:
        print("no moov/mdat pair; nothing to do")
        return 0
    if typ_list.index("moov") < typ_list.index("mdat"):
        print("moov already before mdat; nothing to do")
        return 0
    if not apply_:
        print("moov is after mdat -- pass --apply to rewrite the file")
        return 0

    if backup:
        shutil.copy2(path, path + ".bak")
        print("backup:", path + ".bak")
    status = mp4faststart.faststart_in_place(path)
    print("status:", status, "size:", os.path.getsize(path))
    return 0 if status in ("rewritten", "already") else 1


if __name__ == "__main__":
    sys.exit(main())
