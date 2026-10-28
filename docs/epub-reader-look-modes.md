# Reader-mode EPUB：原书外观 / 夜间 / 跟随主题

Reading mode 导入（"保持章节原始版式和图片"）保留章节 HTML、图片、字体和书的
CSS，词条由 `lute-epub-reader.js` 注入原文。此前章节渲染在 Lute 主题背景上、
被钳在 46rem 窄栏、暗色主题下书的文字颜色在导入时被丢弃，观感与原书差距大。
2026-10 起阅读界面提供三种外观（阅读菜单，`localStorage` 键 `epubLookMode`，
默认 `original`）：

| 外观 | 效果 |
| --- | --- |
| 原书外观 `original` | 灰底上的白纸页面（边距、阴影，观感对齐 PDF 页），挂**保留原色**样式表 `lute-orig.css`，书的配色/背景完整生效；纸张宽 60rem |
| 夜间 `night` | 暗纸面（#16161d），整章 `invert(0.88) hue-rotate(180deg)`，图片/视频反向还原 |
| 跟随主题 `theme` | 旧行为：主题背景、`lute.css`（未涂底块的 `color` 在导入时丢弃）、46rem 栏 |

## 导入产物（`lute/book/epub_extract.py`）

- `lute.css` —— 跟随主题变体（颜色降级，同旧版）；
- `lute-orig.css` —— 保留原色变体（`scope_css(..., keep_colors=True)`）；
- `chapters.json` —— `[{index, path, viewport}]`，各章在压缩包内的源路径与
  固定版式视口，供懒重建；
- 固定版式章节（xhtml `<meta name="viewport">` 声明像素宽高，如绘本）的
  wrapper 带 `data-epub-viewport="WxH"` 与 `epub-fixed` 类。

## 旧书兼容（`ensure_orig_css`）

每次渲染 epub 页面时若包内缺 `lute-orig.css`（本次改动之前导入的书），从解压
包里的**原始样式表**重建一次（原子写入）：有 `chapters.json` 按源路径精确收
集，没有则扫描包内全部内容文档。因此旧书无需重导入即可使用原书/夜间外观。

**例外**：固定版式支持依赖导入时写入的 `data-epub-viewport`，旧书要获得固定
版式渲染需重新导入。

## 渲染要点

- 两份样式表 URL 随章节片段下发（`data-epub-css` / `data-epub-orig-css`），
  `lute-epub-reader.js` 按当前外观挂 `<link>`，切换即换 href。
- 纸张画在 `#epub-chapter` 上（非 id 锚定到 `.epub-body`），书的
  `body { background }` 规则可以覆盖默认白纸。
- **缩放**：阅读菜单的放大/缩小/适宽对 EPUB 生效（`localStorage` 键
  `epubZoom`，与漫画/PDF 相互独立）。重排章节用 CSS `zoom` 作用于
  `#epub-chapter`——文字等比缩放、栏宽自动展开；图片受 `max-width: 100%`
  钳制在栏内（放大时随栏变宽、缩小时等比缩小），这是重排文本的标准电子书
  语义。固定版式页面由适宽计算（fit × zoom）整体缩放，可平移。
- **固定版式**：`#thetext.epub-fixed-container` 走 PDF 同款固定页机制
  （`_isMangaReader` / `_fitMangaPage` / 面板内滚动），每章一页。

## 移动端独立渲染页（`/read/epub_render/<bookid>/<pagenum>`）

LuteForMobile 的阅读器是原生 Flutter 渲染,不能跑网页版的 mount 流程;它用
WebView 加载这个独立页获得同样的原书外观。页面是精简的完整 HTML 文档:

- 结构与网页阅读页一致(`div#thetext.epub-text-container` + 外观 class +
  `#epub-chapter` + 两个 JSON 节点),复用 `styles.css` 与
  `lute-epub-reader.js`,`EpubReader.mount` 注入词条 span,流程与网页相同;
- 用户主题 CSS(`/theme/current`)照常下发,`theme` 外观与网页一致;
- 查询参数:`?look=original|night|theme` 预置外观(避免加载闪错主题)、
  `?furigana=0|1` 覆盖语言的振假名默认(不传则跟随语言设置);
- 振假名:`lute-furigana.js` 的 `annotate()` 不检查 `enabled`,所以"关"
  必须"不加载脚本"——默认关时模板不输出该 `<script>`,开启由桥接动态注入;
- 内联桥接脚本(`window.LuteEpubBridge`):
  - `setStatus(wid, cls)` 更新词条状态色(单击/双击手势由 JS 时间窗区分后
    经 `flutter_inappwebview.callHandler` 上报原生端);
  - `setLook(mode)` / `setFurigana(on)` / `setZoom(f)` / `zoomIn` /
    `zoomOut` / `resetZoom` / `restoreScroll(top)`;
  - 固定版式适宽与缩放逻辑从 `read/index.html`(`_fitMangaPage` 的
    epub-fixed 分支、`_applyEpubZoom`)**复制移植**,不与网页共享代码,
    网页阅读页不受影响;
  - 滚动位置经 `luteEpubScroll` 上报(`{top, max, fixed}`),由 App 持久化
    并在加载后经 `restoreScroll` 恢复。

网页阅读页(`/read/start_reading` 片段 + `read/index.html`)完全不变。

## 相关测试

- `tests/unit/book/test_epub_reader_mode.py` —— keep_colors、双样式表、
  chapters.json、viewport 检测、懒重建；
- `tests/js/run_epub_layout_harness.py` —— 三种外观切换、zoom 语义（浏览器级）。
