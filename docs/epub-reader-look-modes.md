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

## 相关测试

- `tests/unit/book/test_epub_reader_mode.py` —— keep_colors、双样式表、
  chapters.json、viewport 检测、懒重建；
- `tests/js/run_epub_layout_harness.py` —— 三种外观切换、zoom 语义（浏览器级）。
