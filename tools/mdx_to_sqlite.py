"""
Convert an MDX (MDict) dictionary into the SQLite file Lute reads.

    python tools/mdx_to_sqlite.py <file.mdx> <out.db>

The output is the two-table dictionary (entries/terms) that both the
reading page's dictionary tab and the phone app query, so a dictionary
converted here can be dropped straight into the server's static folder:

    python tools/mdx_to_sqlite.py shogakukan_v2.mdx \
        lute/static/dict/ja_zh_shogakukan_v2.db

That folder is a pool: every *.db in it is offered on each language's
settings page, and the language picks the one it reads.  Naming the file
ja_zh_shogakukan_v2.db is what makes the phone app's built-in download
URL work with no setup; any other name is fine for the web reader.

The settings page can also import an MDX, which is easier for anything
under the 200 MB upload limit; this tool is the way in for the larger
dictionaries.  Both paths run the same converter
(lute/dict/importer.py), so the result is identical.

No third-party dependency: the MDX reader is Lute's own (lute/dict),
ported from the phone app's parser.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lute.dict.importer import convert  # pylint: disable=wrong-import-position


def _report(processed):
    print(f"  {processed:,} entries read", flush=True)


def main():
    "Convert argv[1] to argv[2], reporting progress as it goes."
    if len(sys.argv) != 3:
        print(__doc__.strip())
        sys.exit(1)

    src, out = sys.argv[1], sys.argv[2]
    if not os.path.isfile(src):
        print(f"No such file: {src}")
        sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    print(f"Converting {src} ...")
    try:
        metadata = convert(src, out, progress=_report)
    except Exception as e:  # pylint: disable=broad-except
        print(f"Failed: {e}")
        sys.exit(1)

    print(f"Wrote {out}")
    print(f"  title:   {metadata['title'] or '(none)'}")
    print(f"  terms:   {metadata['terms']:,}")
    print(f"  entries: {metadata['entries']:,}")
    print(f"  size:    {metadata['bytes'] / (1024 * 1024):.1f} MB")


if __name__ == "__main__":
    main()
