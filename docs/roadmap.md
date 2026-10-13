# Song / lute-v3 路线图

> v2 更新 2026-09-20，基线 `all-features-combine` = `4da2ddb7`（在 v1 基线 `574966df` 之上 +10 commit，其中 1 个未推送）。
> v1 体检 2026-09-19（`574966df`，生产 `46273953`）。本版合并两条工作线：v1 的「语法引擎验证」主线，以及同日工程清单的 Phase 1/2——后者已基本执行完毕（见 §二）。

## 一、一句话结论

**工程减肥（原 Phase 1/2）已基本落地**。当前真正的未了事项只剩三类：

1. **部署积压**——生产落后本地 ≥12 commit；mp3 修复、book-type 注册表、播放器去重等成果全都还没到生产；
2. **Track 1：12 个语法引擎仍不可度量**——v1 以来未动的主线，也是唯一能让「13 种语言」这句话站得住的工作；
3. 几件半天量级的收口（tts 环境失败、生产目录残留、かぐや姫确认；`/sw.js` 已随 2026-10-14 PWA 下线整体移除）。

## 二、v1 → v2 已完成（逐项对应提交）

| 事项 | 提交 | 备注 |
| --- | --- | --- |
| mp3/字幕书编辑页 Text 上传 desync | `94a93205` | 字幕书忽略杂散 Text 上传 |
| CI 触发 `all-features-combine` push | `da94b825` | 此前主战场 push 不跑 CI |
| 根目录一次性脚本清除 | `d48988fb` | `fix_*.py` 已不在 |
| book-type 单一注册表 | `4da2ddb7` | `lute/book/types.py` 为唯一来源；编辑表单下拉、`SUBTITLE_BOOK_TYPES`、datatables 过滤白名单、read/book 路由、导入选择器、前端图标 chips（`window.LUTE_BOOK_TYPES`）全部派生 |
| 播放器公共引擎 | `4da2ddb7` | `media-player-base.js` 承载共享引擎；yt 1270→465、bili 1155→552（适配层 -58%）；bili 的 transcript 滚动升级为 yt 的健壮版 |
| 主题 token 层 | `4da2ddb7` | 语义 token + guard-var 模式；迁移路径见 `themes/css/README.md` |
| vstatic 收尾 | `4da2ddb7` | 12 个模板 27 处 `url_for('static')` 迁完；剩余 `?v=` 均为主题 CSS 的内容哈希（正常机制） |
| 测试基建 | `4da2ddb7` | 常驻命名内存库（每 worker 建一次、数据间清除）、fsync off、`pytest -n 4`（xdist 3.8.0 已装） |
| 缺 parser 测试自动 skip | `c2a99470` | v1 Track 0.4 的 5 个 korean 失败转为 skip |
| black 全分支 / pylint 门禁 / CI 缺依赖降级 / epub 断言更新 | `4ec2ce4e` `585e8554` `160c5518` `eeb26372` | 卫生类 |

另经本轮（9-20）核查确认的既有状态，此前被误列为待办：

- **start_reading 首开竞态已修**：`224cb7ee`，`IntegrityError` → rollback + 回查已存在 term + 重试（`lute/read/service.py:594` 起）。
- **生产语法引擎依赖不是缺口**：v1 生产核查确认 sudachipy / kiwipiepy / spacy / pymorphy3 / pythainlp / pyarabic 全部已装，语言页有 `grammar_engine_status()` 状态行 + 一键安装。

## 三、体检数据变化

| 项 | v1 (9-19) | v2 (9-20) |
| --- | --- | --- |
| 测试 | 1077（1070 passed / 6 failed / 1 skipped） | 1185 passed / 14 skipped / **0 failed**（实测 `pytest -n 4`，4m01s；skipped 为缺 parser/依赖类，见 §六） |
| yt/bili 播放器合计 | 2,425 行 | 1,017 行 |
| `static/js` 合计 | 10,926 行 | 10,470 行 |
| 领先 `upstream/develop` | 529 | 539（含 1 个未推送 commit） |
| CI 覆盖 | 主战场分支不触发 | push 即触发 |
| 词表 JSON 外置（LFS） | 未做 | 未做（可选项，状态不变） |

## 四、剩余路线图

### Track 0 — 收口 v2（半天量级，最先做）

| # | 事项 | 现状证据 | 做法 | 验收 |
| --- | --- | --- | --- | --- |
| 0.1 | 部署积压（升级版） | 生产 `46273953`，本地领先 ≥12 commit；`4da2ddb7` 尚未推送 | push + `deploy.sh` + 确认服务重启 | 生产 = `4da2ddb7`；`lute.service` active |
| 0.2 | 生产目录残留 | `/opt/lute` 有 `backup/predeploy_*`、`bulk_import_erin.py`、`deploy.sh.bak_*` | 归档或删除（**不碰 `lute_data/`**） | `git status` 干净 |
| 0.3 | `/sw.js` 生产 404 复测 | 路由、`lute/static/sw.js`、注册代码均已随 `fd7c1869` 进入生产分支，但 9-19 实测 404 ⇒ 疑似服务进程未重启或发布物与 checkout 不一致 | 随 0.1 部署重启后 `curl /sw.js` 复测；仍 404 再按 v1 的二选一（修发布 / 删注册） | console 无 404，SW 注册成功 |
| 0.4 | tts dropdown 环境性失败 | 本地复测 0 红（1185/14/0）；但该测试在部分环境失败（见 `4da2ddb7` 提交信息） | 装依赖或 `skipif` 注明原因，让任何环境都 0 红 | 全量结果可判定（0 红） |
| 0.5 | かぐや姫 重导（待确认） | 工程清单遗留项，v1 审计未覆盖其现状 | 若 CJK 拆分修复后的数据仍异常：删除 + 重导；否则关闭 | 关闭或显式放弃 |

> 0.1 是当前性价比最高的单项：所有已完成的修复（mp3 desync、注册表、播放器、主题）都压在本地没生效。

### Track 1 — 把 12 个引擎从「看起来对」变成「可度量」（最高价值，v1 原样）

| # | 事项 | 做法 | 验收 |
| --- | --- | --- | --- |
| 1.1 | 泛化测量工具 | `scripts/measure_grammar_panel.py` 加 `--engine en|es|...`，走对应 `analyze_*` + 该语言 tokenizer | `python -m scripts.measure_grammar_panel --db <prod> --engine en` 有输出 |
| 1.2 | 建语料 | en/ja 直接用生产库（`--db`，只读）；ko 19 页；其余 10 种从公开文本（Gutenberg / Wikipedia 摘要）各取 50–200 页，**不入库** | 每种语言一份 `/tmp/<lang>_corpus.hex` |
| 1.3 | 出第一批数字 | 每语言：条目/页、**零命中规则清单**、单条占比 top10、重名行率 | 一张对照表 |
| 1.4 | 按数字处置 | 零命中 ⇒ 删或补 spec；占比 >20% 页的规则 ⇒ 降噪（照 ja 的折叠/静默名单机制，不按文本形状猜）；无法达标的语言 ⇒ UI 标「实验性」 | 每语言条目/页落在 3–15、单条占比 <10% |
| 1.5 | 守住结论 | 每语言加「语料级」回归（哪怕只断言条目/页区间与零命中 = 0） | 进 `tests/`，改引擎会红 |

> 顺序建议：**en（语料最足、规则最像日语）→ zh/yue（中文用户基数最大）→ ko → 其余**。
> v1 结论不变：ja 是唯一经过语料级验证的引擎（400 页语料、13.6 条/页基线）；其余 12 种只有「规则在自己例句上命中」级别的证据；生产语料除 en/ja 外几乎为零（多数 1 书 1 页）。

### Track 2 — ja 面板的已知剩余（有意留下的，原样保留）

| # | 事项 | 现状 | 判据 |
| --- | --- | --- | --- |
| 2.1 | 手写裸 `て` 正则 2 条 | `te_wa_ikemasen` / `te_mo_ii_desu` 未改 token spec，收益近中性 | 若继续改，须跑 `measure_grammar_panel` before/after |
| 2.2 | 面板只吃当前屏可见段落 | 浏览器条目数天然少于整页离线跑 | 产品判断：要不要改成整页？注意响应体积（当前中位 5.4KB） |
| 2.2b | ~~词条列表筛选标签与行为相反~~ **已修（`7fdc7a74`）** | 标签改为 **"Terms without a parent"**，与既有 SQL `parents.parentlist IS NULL` 一致；**查询结果零变更**。id 仍保留 `filtParentsOnly`——它同时是 localStorage 里筛选状态的键，改名会静默丢掉用户已存的筛选。上游 `upstream/master` 带的是同一个错标签，非本仓库引入 | 已加两个测试：查询语义（T→P→G 中只有 G 无 parent，只有 G 返回）+ 渲染标签。注：`/term` 本身没有路由，列表在 `/term/index`，所以此前没有任何测试断言过这个页面的 markup |
| 2.3 | term 层 lemma / parent | 8,119 日语词条只 **257（3.2%）** 有 `wordparents`，且全部由手动路径产生（有 parent 的词条里 status 0 为 **0 个**） | **已干跑出上界**：1,414（17.4%）全假名恒定拿不到 lemma；接进开书路径可得 **1,061（13.1%）**，其中 543 条是未知词；代价是**新建 337 个 parent 词条**；4.5% 的 lemma 是拼接式（`似ている`→`似るいる`）须规则过滤。工具：`scripts/probe_term_parents.py`（只读）。落地顺序：P1 干跑 → P2 回填（**sync 默认 0**，先库副本演练）→ P3 只对新书单词条挂 → P4 给 `ParsedToken` 暴露 per-token lemma。 |
| 2.4 | `〜ている` 等的被动形态 | 刻意不加 `動詞` 前置约束（会丢 `言われています`） | 保持现状；改动须整轮复测 |
| 2.5 | 命门文档化 | `_jp_fragments` 顺序、`_HAND_WRITTEN_LITERALS` 收集范围、`_is_specific` 只用于命名 | 都已有 docstring + 测试守，别动 |

### Track 3 — 工程债剩余

| # | 事项 | 现状 | 做法 |
| --- | --- | --- | --- |
| 3.1 | 分支清理 | 16 个本地分支，11 个停在 2026-07 | 确认已合入 `all-features-combine` 后删除；保留 `song`、`develop` |
| 3.2 | 上游同步节奏 | 领先 539 commit，上游极慢 | 固定「每 N 周 cherry-pick 净新增」+ 校 `git diff upstream/develop..HEAD -- <文件>` 只剩自有改动 |
| 3.3 | `sw.js` 预缓存未版本化 | 40 条裸 `/static/js/*.js`，CF immutable 一年 | 预缓存改走 `vstatic` URL（前提：0.3 决定保留 SW） |
| 3.4 | pip-from-web 路由复核 | `POST /language/grammar_engine/install/<extra>` 会 `pip install` | 已有白名单（`_ENGINE_INSTALL_SPECS`）+ 超时；复核鉴权与 CSRF 是否齐全 |
| 3.5 | 前端护栏 | 唯一护栏是 `test_static_assets.py`（9 项），无 JS 单测 | 可选：最小 `node:test` 覆盖「点击 vs 拖选」「环清理」这类回归过两次的逻辑 |
| 3.6 | 11MB 词表 JSON 入库 | `lute/stats/*_data` 已进 git | 可选：评估改按需下载或 LFS（注意离线可用性） |

### Track 4 — 产品面（用户视角）

- **阅读页 / 词条层**：2.3 的 lemma parent 是「点开一个变形动词能看到词典形与相关形」的前提（3.1% → 高覆盖的唯一路径）。
- **inline reading / ruby 显示**：`Term.romanization` 基建已在，差读页渲染层——工程清单遗留的产品向候选。
- **「Mark rest known」误触保护**：翻页箭头 + 开关误触导致整页词变 known，加确认或可撤销。
- **Stats 页**：JLPT/HSK/CEFR 掌握度表的后续（与 `stats/*_data` 的扩张同源）。
- **漫画**：`_edit_manga()` 已支持「替换压缩包」重导；运维项仅剩「换包时清理孤儿 `static/manga/<uuid>/`」（**别批量删**）。

## 五、建议顺序（v2）

1. **Track 0 全做**（半天量级）——尤其 0.1：把已完成的成果真正部署出去，信号变干净。
2. **Track 1.1 + 1.2 + 1.3（先 en）**——泛化测量工具 + 第一份非日语数字。这一步直接告诉你：另外 12 个引擎是「能用」还是「噪声源」。
3. 按 1.3 的结果决定 1.4 的力度，再按 Track 2/3 清理与 Track 4 的产品节奏推进。
4. Track 3.1/3.2 可与 2 并行（纯 git 操作，不碰代码）。

## 六、未验证项（诚实清单）

- 全量 pytest 复测已完成（2026-09-20，`pytest -n 4`）：**1185 passed / 14 skipped / 0 failed，4m01s**。与 `4da2ddb7` 提交信息（1197 passed / 1 skipped / 1 failed）的差异来自环境差异：本机缺部分 parser/依赖的用例被 skip 而非通过；tts dropdown 未复现失败。14 个 skip 的具体名单未逐条核对。
- 生产实际 HEAD 未复核（本地无法核查；「落后 ≥12 commit」是按 v1 审计 + 新增 10 commit 推算）。
- 0.5 かぐや姫 的生产数据现状未验证。
- ko 与其余 10 种引擎的行数、零命中、误报率仍未知（v1 结论不变，本轮未推进）。
- 11MB 词表 JSON 外置可行性未评估。
