"""
Build a review artifact for the Korean panel's new blocks.

Runs the real pipeline end to end for a preview page whose sentences are the
19 merged rows' own curated examples:

    analyze_korean(page, "zh")          -> the panel payload the route returns
    node grammar_panel_js_harness.js    -> the markup the real lute-commands.js
                                           builds from that payload

so the HTML in the artifact is the module's own output, not a hand-written
mock.  The only thing the wrapper adds is the panel container and a rule that
folds the reference/notes blocks open, since they ship inside a <details>.

Usage:  python scripts/grammar_materials_ko/build_panel_preview.py
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, BASE)

from lute.read.render import grammar_analysis_ko as G  # noqa: E402

NODE = "/Users/cxi/.workbuddy-ai/binaries/node/versions/22.22.2-2/bin/node"
HARNESS = os.path.join(BASE, "tests", "unit", "read", "grammar_panel_js_harness.js")
MODULE = os.path.join(BASE, "lute", "static", "js", "lute-commands.js")
OUT = os.path.join(HERE, "panel_preview_2026-09-30.html")

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>韩语语法面板：接续 / 参考例句（带高亮）/ 注意点</title>
<link rel="stylesheet" href="../../lute/static/css/styles.css">
<style>
  body { font-family: system-ui, sans-serif; margin: 0; padding: 24px 32px 64px;
         background: #f6f7f9; color: #1b1f24; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .lede { color: #5b6472; font-size: 13px; margin: 0 0 20px; max-width: 900px; }
  .lede code { background: #eceff3; padding: 1px 4px; border-radius: 3px; }
  .wrap { max-width: 900px; }
  .panel-host { background: #fff; border: 1px solid #dfe3e8; border-radius: 8px; }
  .note { font-size: 12px; color: #6b7280; margin: 18px 0 6px; }
</style>
</head>
<body>
<div class="wrap">
<h1>韩语语法面板：新增的三个块</h1>
<p class="lede">
  下面是 <code>analyze_korean(page, "zh")</code> 的真实输出，交给<b>真实的</b>
  <code>lute/static/js/lute-commands.js</code> 渲染出来的面板（用
  <code>tests/unit/read/grammar_panel_js_harness.js</code> 跑，stub 掉网络与右栏）。
  页面句子取自 19 条有匹配器的合并行各自的参考例句。
  折叠区在本预览里被强制展开，实际使用时点标题才展开。
</p>
<p class="note">
  注意：这里的"页面例句"和"参考例句"看起来是同一句，因为预览页就是拿参考例句拼的。
  真实阅读时两者不同 —— 页面例句来自当前页命中的句子，参考例句是词条自带的（书里的原句）。
</p>
<p class="note">共 %(count)d 个条目，其中 %(with_ref)d 个带参考例句、
%(with_formation)d 个带接续行、%(with_notes)d 个带注意点。</p>
<div class="panel-host">
<div class="grammar-analysis-panel">
%(panel)s
</div>
</div>
</div>
<script>
  document.querySelectorAll("details.grammar-item__more").forEach(function (d) {
    d.open = true;
  });
</script>
</body>
</html>
"""


def main():
    rules = [r for r in G._get_pattern_rules() if r["key"].startswith("kgm_")]
    live = [r for r in rules if r["reference"]]
    page = "\n".join(r["reference"]["korean"] for r in live)

    entries = G.analyze_korean(page, "zh")
    print(f"live merged rules: {len(live)}   panel entries on the page: {len(entries)}")

    proc = subprocess.run(
        [NODE, HARNESS, MODULE],
        input=json.dumps({"data": entries}),
        capture_output=True,
        text=True,
        check=True,
    )
    panel = json.loads(proc.stdout)["html"]

    html = PAGE % {
        "count": len(entries),
        "with_ref": sum(1 for e in entries if "reference" in e),
        "with_formation": sum(1 for e in entries if "formation" in e),
        "with_notes": sum(1 for e in entries if "notes" in e),
        "panel": panel,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"wrote {OUT} ({len(html)} bytes)")


if __name__ == "__main__":
    main()
