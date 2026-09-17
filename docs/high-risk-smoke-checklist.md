# 高危区 Smoke 清单

> 用途：这些区域改一行就可能静默坏掉，且**默认 `pytest` 完全覆盖不到**——
> `.pytest.ini` 里 `--ignore=tests/acceptance/ --ignore=tests/playwright/`，
> 浏览器级套件平时根本不跑。改动前后各过一遍，比事后回滚便宜。
>
> 覆盖情况一栏是**实测**的（在 `tests/` 里检索过），不是估计值。

---

## 三层护栏

| 层 | 触发 | 命令 | 覆盖 |
|---|---|---|---|
| L1 本地快检 | 每次改完 | `./venv/bin/python -m pytest` | 857 项，~23 分钟，**不含**任何浏览器套件 |
| L2 浏览器套件 | 改动高危区时手跑 / nightly 自动 | `inv accept` → `inv acceptmobile` → `inv playwright` | 55 项 acceptance + 4 项 @mobile + playwright |
| L3 nightly | 每天 02:00（北京） | `.github/workflows/nightly-guardrail.yml` | 同 L2，自动跑，失败留截图 artifact |

**L2 必须串行**：三套都起 5001 端口并清空 test db，并发会互相踩。

---

## 基线（2026-09-13：L2 首跑 → 清理后）

这之前 L2 **从未在本分支跑过**，首跑结果就是基线。清理后已全绿。

| 套件 | 首跑 | 现在 |
|---|---|---|
| `inv accept` | 50 passed / **5 failed**（241s） | **55 passed / 0 failed**（192s） |
| `inv acceptmobile` | 全绿 | **4 passed / 0 failed**（10s） |
| `inv playwright` | 1 passed / **2 failed**（48s） | **4 passed / 1 skipped**（28s） |

首跑 7 条失败的归因（**没有一条是产品回归**）：

| 失败用例 | 归因 | 处理 |
|---|---|---|
| `test_book.py::test_i_can_import_a_text_file` | **flaky** — 隔离重跑即通过 | 未改 |
| `test_dict_popup.py::test_dictionary_popup_closed_on_unload` | **陈旧选择器** — 阅读页已改汉堡菜单，`[title="Home"]` 全仓不存在 | `.hamburger-btn` → `#reading_menu a[href='/']` |
| `test_reading.py::test_user_can_add_and_remove_pages` | **陈旧选择器** — 模板里已无 `id="text"` | — |
| `test_reading.py::test_page_start_date_is_set_correctly_during_reading` | **陈旧选择器** — footer 已删，无 `#footerNextPage` | 改点 header 的 `#navNext` |
| `test_reading.py::test_peeking_at_page_does_not_set_current_page_or_start_date` | **外网依赖** — 词典 tab 图标回退到 `google.com/s2/favicons`，无外网时 5 个请求挂住 → `load` 不触发（路由本身 200/0.02s） | 产品侧删掉该回退：图标只走自托管（`dict-tabs.js`） |
| `playwright::test_playwright` | fork 的 **fit-to-screen 分页**把非当前屏的词 `display:none` | 先翻屏（`_reveal`） |
| `playwright::test_page_change_first_word` | 上一次点击**残留 hover**，`LUTE_CURR_TERM_DATA_ORDER` 从悬停词起算 | 先 `_park_mouse` |

顺带查出的 **2 个真实产品 bug**（与本套件无关，顺手修）：

1. `bookmarks/list.html` 没引 `book-listing-shared.js` ⇒ 书签行的「…」菜单永远打不开，Edit / Delete 点不到。
2. `read/routes.py:new_page` 渲染 `page_edit_form.html` 时漏传 `page_cues` / `cue_audio_url` ⇒ `{{ page_cues | tojson }}` 抛 `TypeError`，**新增页永远 500**。

### 三条「看着像产品回归、其实是测试没等」的竞态（已修）

1. **阅读页文本是异步 swap 进来的**：`htmx.ajax` 写入 `#thetext`，而 `_finishPageSwap()` 末尾会调
   `start_hover_mode()` → `_hide_term_edit_form()`，把词条表单/词典/光标一起清掉。在 swap 落地前碰文本的步骤会被就地"撤销"。
   - 症状 A：`I hover over "otro"` 的 `count == 1` 断言拿到 **0**（词还没渲染）。
   - 症状 B：词条表单刚打开就被清成 `/read/empty`（空白 iframe）。
   - 修法：`LuteTestClient.wait_reading_ready()`（等 `luteStartReadingDone`）；`click_word()` 内调用，`when_hover` 显式调用。
2. **无效保存的校验提示是随 POST 响应写回 iframe 的**：只读一次 `iframe.content()` 会读到**上一份文档**；
   而 `page.frame(name=...)` 在表单导航的一瞬间还会返回 `None`。
   - 修法：`then_reading_page_term_form_iframe_contains` 改用 `frame_locator("#wordframeid")` + `to_contain_text()`（自动重试）。
3. **`h` 热键（ToggleHighlight）会 `location.reload()`**：下一步可能在 reload 落地前就开始动手，改完又被打回。
   - 症状：`test_toggling_highlighting_only_shows_highlights_on_hovered_terms` 间歇性在 `displayed_text()` 的
     `wait_for_selector('span[class*="textitem"]')` 上超时。
   - 修法：`press_hotkey()` 结尾 `wait_for_load_state("load", timeout=2000)`（不导航的热键是 no-op）。

> 服务端日志里反复出现的 `StaleDataError / PendingRollbackError`（`app_factory.py:183 inject_menu_bar_vars`）
> 在整个 run 中持续存在，**通过和失败的用例都会出现**，与上述失败无因果 —— 是独立的既有噪声。

---

## 高危区清单

| # | 区域 | 现有自动覆盖 | 缺口 | 手工验证要点 |
|---|---|---|---|---|
| 1 | **刷新同步**（改状态后阅读帧/列表同步） | ✅ `reading.feature`「Updating term status updates the reading frame」(@mobile)、「Learned terms are applied to new texts.」；`sync_status.feature`「Can link child and single parent term.」「Linking multiple parents breaks status updating.」 | 三者同步视图（阅读页 / 列表 / 词列表）**同时**打开的交叉一致性 | 开两个标签页，A 页改状态 → B 页刷新，确认无陈旧计数 |
| 2 | **列显隐**（book list 列开关） | ❌ 无 | 全无 | 关掉 Status / New word 列 → 确认不发 `/table_stats` 请求；再打开 → 数据回填且不闪烁 |
| 3 | **Quick Set Status Mode** | ❌ 无（`reading_menu.html` 的 `tap_sets_status`） | 全无 | 开关切换后，阅读页单击词是否直接置状态；与 hotkey 1-5 是否冲突 |
| 4 | **网页导入** | ⚠️ 部分：`book.feature`「I can import a url.」 | 真实外网页面（含编码/重定向） | 导入一个非 UTF-8 页面，确认不乱码、不静默截断 |
| 5 | **PDF 书** | ⚠️ 仅单元级（`unit/book/test_stats.py` 等）；acceptance 有 `Hola.pdf` 素材但无 PDF 专属场景 | 「New word 恒 0%」类回归只有单测兜底 | 导入 PDF → Stats 页 New word% 非 0；页数/进度条正确 |
| 6 | **漫画分页**（`_splitToScreens`，`lute.js:1993-2000` 的 rAF 重排） | ❌ **零覆盖**（`tests/` 里检索 `manga` / `_splitToScreens` / `mokuro` 无命中） | 全无 | 改窗口宽度 / 缩放 → 屏幕切分重算，不丢字、不错位；跨屏导航边界 |
| 7 | **词条弹窗定位**（jQuery UI tooltip） | ❌ 无（acceptance 有聚焦/热键场景，但无弹窗定位断言） | 全无 | 折行词（行尾换行）弹窗要贴着**光标那一行**，不能偏 400px；关闭要即时（无 ~400ms 淡出残留） |
| 8 | **触摸点击反馈** | ❌ 无（`tap-pressed` / `tap-ack` / haptics 在 `tests/` 无引用） | 全无 | `localStorage.screen_interactions_type='mobile'` + reload，再测四态与震动开关 |
| 9 | **主题系统** | ⚠️ 仅单元级（`unit/themes/test_service.py` 测 CSS 拼接） | 渲染层无覆盖 | 切主题看 `#status` 选中态对勾是否可见（亮色主题易隐形） |
| 10 | **备份/恢复迁移** | ✅ `unit/backup/test_restore_migration.py` | 上游 `.db.gz` 恢复后 Song 专属迁移 | 恢复后重启，确认 `LgKiwi*` 四列存在 |
| 11 | **Bilibili 播放**（DASH 中继 + 官方播放器降级 + 画质档位） | ✅ `unit/book/test_bilibili.py`（31 项：URL 解析、上游失败→502 JSON、代理透传）+ `unit/book/test_bilibili_quality.py`（25 项：默认最低码率、AVC 优先、多档 MPD 为合法 XML、`q=` 路由、档位菜单护栏）+ `unit/book/test_bilibili_embed_fallback.py`（5 项：降级态不被覆盖层遮挡） | 真实网络的取流与播放 | 服务器出口 IP 被 B 站风控（`-412 request was banned`）时中继**永久不可用**，与代码无关。确认 `LUTE_BILIBILI_PROXY` 已配且隧道在线 → 出画面且**字幕跟随正常**；把隧道断开再刷新 → 应自动降级到官方播放器（能看视频、字幕不跟随），且在官方播放器上**点得动播放/暂停/音量**；齿轮里的 Quality 应显示**最低档**且切档后画面继续 |
| 12 | **漫画书编辑 / 重导**（`/book/edit/<漫画书>` 覆盖原书） | ✅ `unit/book/test_manga.py`（8 项：编辑页只有漫画控件、改标题标签不动图片、上传新包换页/图/mokuro、页数增减、坏扩展名与空白标题被拒、纯文本书仍走通用编辑页） | 浏览器里真实选包上传、数百页大包耗时 | 列表点 Edit → 只出现漫画页（**无文本框、无 Type 下拉**）；只改标题标签 → 图片目录与页数不变；传一个新包 → 页数变成新数、`/read/<id>/page/1` 显示新图，**书 id 不变**（阅读记录仍在）；传 `.rar` / 空标题 → 报错且书不变 |

---

## 漫画书：编辑页 = 换包重导（覆盖原书）

`/book/edit/<id>` 现在对 `book_type == "manga"` **分流**到 `_edit_manga()`（`lute/book/routes.py`），渲染 `book/edit_manga.html`：只有标题 / 标签 / 「替换压缩包」。

- **旧行为为什么必须改**：漫画书渲染的是通用文本编辑页 —— 文本框恒空（漫画页文本本来就是空占位），而 Type 下拉里**没有 manga 这个选项**，一旦保存就把书悄悄改成文本书（页还是那些空页）⇒ 一本书直接坏掉，且不报错。
- **重导语义**：书 id、语言、标签、词的状态都不变；`manga_path`、`manga_data`、页行（每页一个空 `TxText`）按新包重建（`BookService.replace_manga()`）。页数可变多可少。
- **页行必须显式 `session.add_all()`**：重导时书已经是 persistent，SQLAlchemy 2.0 不再对「往持久父集合里 append 瞬时子对象」做 backref cascade，只发一条 `SAWarning`。症状是**页数静默变 0**（`page_count == 0`），页面全黑但无异常。旧数据的 `wordsread.WrTxID` 走 `ondelete=SET NULL`，所以已读词不丢。
- **旧的 `static/manga/<uuid>/` 故意不删**：书的 `BkMangaPath` 是它的唯一引用，留着重放旧 `.db.gz` 备份时仍能找到图（与「别批量删孤儿目录」的约定一致）。
- 副作用（可接受）：书内「已读页」进度归零（内容已经换了），页面书签随页行一起被清掉。

---

## 部署前 5 分钟 Gate

改过上面任意一行时，部署前跑：

```bash
cd /Users/cxi/Documents/lutedev/lute-v3

# 0. 清掉沙箱 mkdir 缺陷留下的基目录，否则 72 个用例会在 setup 阶段集体 PermissionError
#    （**别用 `rm -rf "$TMPDIR"pytest-of-*`**：zsh 下 glob 无匹配会报 `no matches found` 并中止整条命令链）
find "${TMPDIR}" -maxdepth 1 -name 'pytest-of-*' -exec rm -rf {} + 2>/dev/null

# 1. 确认没有别的 pytest 在跑（测试库是固定共享路径，只能独占）
pgrep -fl pytest || echo "clean"

# 2. 清沙箱代理对 localhost 的干扰（不设这个，_site_is_running 会拿到 502 而直接抛错）
export NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1

# 3. 本机没有 playwright 自带 chromium，用系统 Edge；并绕过代理
export LUTE_TEST_BROWSER_CHANNEL=msedge
export LUTE_TEST_BROWSER_ARGS=--no-proxy-server

# 4. tasks.py 用的是裸 python/pytest，需要 venv 在 PATH 前面
export PATH="$PWD/venv/bin:$PATH"

# 5. 串行跑（都占 5001，别并发）
./venv/bin/python -m invoke accept
./venv/bin/python -m invoke acceptmobile
./venv/bin/python -m invoke playwright
```

**收尾**：`pgrep -fl pytest` 核实无残留进程。

---

## 已知环境坑（不是代码问题）

| 现象 | 真因 | 处理 |
|---|---|---|
| `_site_is_running` 抛 `RuntimeError: Got code 502` | 沙箱设了 `HTTP_PROXY` 但没 `NO_PROXY`，`requests.get("localhost:5001")` 被代理拦截 | 设 `NO_PROXY=localhost,127.0.0.1` |
| 浏览器起不来 / 超时 | `~/Library/Caches/ms-playwright/` 为空，未下载自带 chromium | 设 `LUTE_TEST_BROWSER_CHANNEL=msedge` |
| 72 个用例 setup 阶段 `PermissionError`，报文含 `pytest-of-` | 沙箱 `mkdir` shim 在目录已存在时仍抛错 | 跑前 `rm -rf "$TMPDIR"pytest-of-*` |
| 大面积 `readonly database` / `no such table` / `disk I/O error` | 两个 pytest 并发，互相删建同一个 test db | 只跑一个；`pgrep -fl pytest` 清残留 |
| 所有浏览器测试都访问不到 5001 | `tasks.py` 里子进程用裸 `python` | 把 `venv/bin` 放到 PATH 最前 |
| Bilibili 书黑屏、接口返回 **HTTP 500** 而非 JSON | 服务器在海外（洛杉矶），B 站 API 按 IP 风控返 `-412`，而路由曾只捕 `ValueError` 兜不住 `HTTPError` | 已修（返回 502 JSON）。长期靠配置出口：`LUTE_BILIBILI_PROXY`，见 `lute/utils/outbound_proxy.py` 与 `utils/bili_egress_proxy.py` |
| 配了代理仍黑屏 | 隧道断了（本机休眠 / SSH 断开 / 代理进程被回收） | 重启 `utils/bili_egress_proxy.py` 与 `ssh -N -R …`；页面会降级到官方播放器，可据此判断 |

---

## Bilibili 出口代理：谁在用它

**隧道不是给浏览器的，是给服务器的。** 打开 `/read/<bilibili 书>` 不触发它——渲染只做纯正则解析（`read/routes.py`），零网络出口。只有服务器自己去取流时才走出口：

| 触发点 | 频率 |
|---|---|
| dash.js 要 MPD 清单 → `stream_info()` → `api.bilibili.com` | 每个 `(bvid, page)` 最多 30 分钟一次（进程内存缓存，重启即清） |
| 播放中取视频/音频分段 → `proxy_stream()` → B 站 CDN | **播放全程持续**（每个 Range 请求都转发） |
| 导入新 B 站书抓标题 → `bilibili_title` | 一次性 |

所以：**任何账号**在该部署上播放 B 站书都会经这条隧道，媒体字节走的是出口机器的**上行带宽**，不是某个浏览器的。隧道断开时页面自动降级，不报错。

**他人从 GitHub 部署不会自动形成隧道**：仓库里没有任何地址、端口或凭据（`git grep 172.236.226.132` 零命中，文档与脚本只用 `root@<server>` 占位）。未设 `LUTE_BILIBILI_PROXY` 时行为与改造前完全一致（`bilibili_proxies()` 返回 `None` → 直连），所以国内服务器、或没被风控的部署**什么都不用配**。

**降级态的不变式**：embed 模式下视频区里除 iframe 外不得叠任何覆盖层。`.yt-player-loading` 是 `inset:0; z-index:1`，而 iframe 默认 `z-index:auto`，覆盖层会吞掉所有点击——表现是"画面在播却点不动、像卡死"。护栏：`tests/unit/book/test_bilibili_embed_fallback.py`（CSS 层级 + JS 侧隐藏的双重断言）。

## 画质：默认最低档，手动升档

**默认播最低码率**，因为字节要过隧道（出口机的上行带宽是瓶颈）：

- `stream_info()` 返回 `videos`（**按码率升序**，第一个即默认）与 `video`（= `videos[0]`）。音频仍是**最高档**——实测某 45P 视频：480p 视频 61 kbps、360p 42 kbps，而音频 102 kbps，**音频才是流量大头**，但听力材料牺牲音质不划算。
- `build_mpd()` 为**每一档**生成一个 `Representation`，各自带 `?q=<index>` 的代理 URL ⇒ 切档不需要重取清单、不丢进度。Representation 顺序 = 档位索引顺序，**别改**。
- 前端：`autoSwitchBitrate.video=false`（dash.js 4.7 已移除 `setAutoSwitchQualityFor`，只能走 `updateSettings`）+ `initialBitrate.video=1` 让首帧就落在最低档；**ABR 开着会自己爬到高档**（本地实测 8 秒内就爬到 480p）。
- 档位菜单在齿轮里，选项由 `getBitrateInfoListFor("video")` 动态生成。**它在 `manifestLoaded` 时还是空的**（实测），代码在 `manifestLoaded` 与 `streamInitialized` 上都挂载并最多重试 8 次。选择按**高度**（如 480）记住，不是按索引。
- **MPD 的 BaseURL 必须 XML 转义**：带 `?page=N&q=M` 后裸 `&` 会让整份清单不可解析，播放器静默不启动。护栏测试用 `ElementTree.fromstring` 真解析一遍。
- 取流优先 **AVC**（`avc1`/`avc3`），HEVC 即使码率更高也排在后面——浏览器解不了 HEVC 时表现是**黑屏且无任何错误**，看起来就像"B 站坏了"。全是 HEVC 时才回退使用。
