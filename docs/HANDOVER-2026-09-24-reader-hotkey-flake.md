# 交接：阅读页状态热键间歇性失败（test_reading.py 系列）

> 分支：`all-features-combine` ｜ 日期：2026-09-24 ｜ 状态：**根因已确认、修复已落地并验证**

## 1. 背景与目标

验收套件 `tests/acceptance/test_reading.py` 及其相关用例
（`test_page_start_date_is_set_correctly_during_reading`、`test_clicked_word_keeps_the_focus`）
间歇失败，**只在 CI 复现，本地 5/5、12/12 全过**。

目标：把这组用例变成确定性通过，而不是靠 CI 重试掩盖。

## 2. 根因（已定论）

**不是产品 bug，是 acceptance harness 的时序 bug。**

`_refresh_browser()` 原本只等应用自己的在途标志（`_pendingStatusUpdate` /
`_pendingTermFormReload` / `_pendingNav`），随后立刻用
`body.innerHTML = '' ; body.innerHTML = content` 重建页面。问题在于：

1. 这些标志是在 **`htmx:afterSwap`** 里被清掉的；
2. htmx 在 `afterSwap` 之后还有一整个 **settle 阶段**（默认 `settleDelay` 20ms）
   才跑 settle tasks —— 恢复被克隆的属性、执行换入片段里的内联 `<script>`
   （`read/page_content.html` 末尾的 `parent.restore_cursor_marker(); parent.add_status_classes();`），
   最后才 `htmx:afterSettle`；
3. 在这个窗口内重建 `<body>`，会把**刚换入的节点摘下来** → settle tasks 与片段
   `<script>` 全都作用在 detached 子树上（**detached 子树里的 `<script>` 不执行**）；
4. 结果：可见 span 保留 htmx 克隆过来的**旧 `class`**（如 `status0`），而服务端渲染的
   `data-status-class` 与数据库里都已是新值 → 测试读到 `Hola/.` 而期望 `Hola (1)`。

空载笔记本上 20ms 总是赢，负载高的 CI 上偶尔输 —— 这正好解释「只在 CI flake」。

**被证伪的旧假设**（保留在此以免重走）：

- ❌「重复绑定 keydown 导致热键触发两次」：探针显示每次按键 `kd=1`。
- ❌「两个 status class 共存、需要客户端重上色」：据此做的产品改动在 1.2s 延迟下
  仍然失败，且事件探针显示**根本没有 `settle|thetext` 事件**发生 —— 证明整个
  settle 阶段没跑在可见 DOM 上。该改动已完整还原。
- ❌「DB 泄漏 / 服务端数据错」：服务端探针 `PROBE_SRV_AFTER_COMMIT rows=[(1,1),(2,0),(3,0)]`
  加 `data-status-class` 读数证明服务端与 DB 一直正确。

另外，本地 flake 还有第二个来源：**另一个 codebuddy 会话并发共享同一 app + 同一个
test_lute.db**（用户已授权 kill，现已干净）。

## 3. 修复（提交时只提交本节文件）

### harness 修复（核心）

`tests/acceptance/lute_test_client.py`

- `_READING_PANE_SETTLED` 增加第四个子条件
  `document.querySelectorAll('#thetext > .htmx-added').length === 0`。
  htmx 给每个换入节点加 `htmx-added` class，并且**每个 settle task 的第一件事**就是把它
  移除；tasks 在一个同步循环里跑完，所以「`#thetext > .htmx-added` 为空」是一个**确定性**
  的「settle 已跑完」信号（且只会在整段结束后才能被观测到）。
- `_wait_for_reading_pane()` 文档重写说明上面这点。
- `_refresh_browser()` 里盲等的 `time.sleep(0.2)  # Hack for ci.` 换成
  `self._wait_for_reading_pane()`。

### 产品侧（并发会话留下的候选修复，与本 flake 无直接因果关系，但都保留）

- `lute/static/js/lute.js`
  - `reset_cursor_marker()` 拆成 `clear_cursor_marks()` + `restore_cursor_marker()`；
    同 DOM 重置（ESC / 翻页）仍走 `reset_cursor_marker()`（先清后恢复）。
  - `prepareTextInteractions()` 在 `$(document).on('keydown', handle_keydown)` 之前先
    `$(document).off(...)`，保证幂等。
- `lute/templates/read/{page_content,manga_page,pdf_page}.html`
  - 换入的片段脚本改调 `parent.restore_cursor_marker()`（不清读者的多选）；
    原先的 `reset_cursor_marker()` 会在 htmx「先插节点、后跑片段 `<script>`」的时序下
    盖掉读者刚做的 shift 选择，导致下一拍热键找不到选中项。

### 守卫测试

- `tests/playwright/playwright.py::test_prepare_text_interactions_is_idempotent`
  —— 断言 document 上 keydown handler 数为 1，且单次 ArrowUp 只发 1 个
  `bulk_update_status` POST。
  **必须独立进程运行**（见第 5 节）。
- `tests/acceptance/test_reading_fragment_swap.py` —— 换入片段保留多选 / ESC 仍清空。
- `tests/acceptance/test_settle_poll_steps.py` —— settle-poll 助手是「轮询」而非
  「读一次」，以及 `_refresh_browser` 先等后重建。
- `docs/high-risk-smoke-checklist.md` 第 4 条补充了该竞态的说明。

## 4. 验证结果

| 条件 | 结果 |
|------|------|
| 注入 `LUTE_TEST_SLOW_MS=1200`（模拟慢 CI，修复前同条件每轮 16–17 失败） | **两轮各 25 passed**（99.12s / 97.57s） |
| 无延迟、无探针，`test_reading.py` | **三轮各 25 passed**（80.18s / 73.58s / 74.25s） |
| 守卫测试 `test_reading_fragment_swap.py` + `test_settle_poll_steps.py` | 7 passed |
| `test_prepare_text_interactions_is_idempotent`（独立进程） | 1 passed in 7.21s |
| 整个 `tests/acceptance`（68 用例，无探针无延迟） | **三轮各 68 passed**（156.25s / 159.28s / 155.65s） |

修复后的事件探针读数：

```
req|post|/term/bulk_update_status| ;; swap|thetext|...status98 kwordmarked ;; settle|thetext|...status99|status99
```

—— `settle` 事件出现了，重上色落定。

## 5. 运行方式（重要）

- 验收套件：`--port 5001`（`book.feature` 硬编码端口），`--basetemp=.pytest-tmp`
  绕开 sandbox 默认 tmpdir 的 `EEXIST`。
- `tests/playwright/playwright.py` 必须**独立进程**跑（CI 里就是
  `inv playwright` → `pytest tests/playwright/playwright.py -s`）。
  把它和 `tests/acceptance` 放进同一个 pytest 进程会报
  `It looks like you are using Playwright Sync API inside the asyncio loop`——
  这是运行入口问题，不是测试缺陷。

```bash
cd /Users/cxi/Documents/lutedev/lute-v3
export PATH="$PWD/venv/bin:$PATH" NO_PROXY=localhost,127.0.0.1
export LUTE_TEST_BROWSER_CHANNEL=msedge LUTE_TEST_BROWSER_ARGS=--no-proxy-server

# 起验收 app（后台）；若 5001 已在跑则跳过
venv/bin/python -m tests.acceptance.start_acceptance_app 5001 &

# 验收套件多轮
for i in 1 2 3; do
  venv/bin/python -m pytest tests/acceptance \
    -o addopts="" --port=5001 --headless --basetemp=.pytest-tmp \
    2>&1 | tee /tmp/accept_$i.txt | grep -E "passed|failed"
done

# playwright 烟雾/守卫测试（独立进程）
venv/bin/python -m pytest -o addopts="" -q -s \
  tests/playwright/playwright.py::test_prepare_text_interactions_is_idempotent
```

## 6. 状态

调查已完成：

1. ✅ 整个 `tests/acceptance`（68 用例）三轮无探针无延迟全绿。
2. ✅ 对照：`LUTE_TEST_SLOW_MS=1200` 下修复前每轮 16–17 失败、修复后 0 失败。
3. ✅ 三份守卫测试全过；`git diff` 中已无任何 `PROBE_*` / 探针。

**尚未提交**（未提交不是遗漏，是等用户确认）：

```bash
git status --porcelain
 M docs/high-risk-smoke-checklist.md
 M lute/static/js/lute.js
 M lute/templates/read/{manga_page,page_content,pdf_page}.html
 M tests/acceptance/lute_test_client.py
 M tests/acceptance/test_settle_poll_steps.py
 M tests/playwright/playwright.py
?? docs/HANDOVER-2026-09-24-reader-hotkey-flake.md
?? tests/acceptance/test_reading_fragment_swap.py
```

> 注：`?? docs/ROADMAP-*.md` / `docs/roadmap.md` 是工作区里既有的未跟踪文件，与本修复无关。