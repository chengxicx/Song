# Song(颂) 路线图 NEXT —— 2026-09-20 增量更新

> 基线：`all-features-combine` @ `6b6019a9`（9-21 02:00 复核；已推至 `40b9eb60`，
> 其后 1 个 commit 因全量套件未跑完而**故意留在本地**，见 §0.9-21 增量）
> 生产落后：`server/all-features-combine` 跟踪引用显示落后 172 commit——该引用未必新鲜，
> 未当作真实生产状态引用；部署前需现场复核
> 背景：`docs/ROADMAP-2026.md`（P0–P3 产品线）与 `docs/roadmap.md`（Track 0–4 工程线）均于 9-20 制定；
> 但随后 9-20 当天即落地了两项当时最高优先级的工作（FSRS 复习队列、lemma parent 回填）。
> 本文件是**增量更新**：已落地项出表、剩余项重排优先级，并新增「部署还账」与「review 验收」两个收口。
> 用法：每条均给现状证据 / 落地挂钩 / 工作量 / 验收，可直接拆任务。

---

## 0. 状态快照（9-20 深夜，git log 实证）

### ✅ 已落地（上一版路线图列为最高优先级，现已完成）

| 事项 | 落地提交 | 说明 |
| --- | --- | --- |
| 应用内 FSRS 复习队列 | `dc1abdb7` 起 6 连击 | `lute/review/`：scheduler（py-fsrs 可选依赖，一键安装）、enqueue（复用 ankiexport criteria DSL）、criteria builder 下拉、settings 页、dashboard、undo 上次评分；`tests/unit/review` 8 个测试文件 |
| lemma parent 回填 CLI | `1b776372` | `lute/term_parent_map/` + `lute/cli/term_parent_backfill.py`；默认干跑、`--commit` 才写、可 undo；幂等（单遍）；生产快照彩排 VERDICT OK |
| book-type 单一注册表 / 播放器公共引擎 / 主题 token 层 / 测试提速 | `4da2ddb7` | 前版路线图 §二 已列 |
| 日语语法面板修复（ます 误读、跨级 hover 错位） | `23b79756` `0c236842` | 生产已上线 |
| zh.json / ko.json 语法释义生成器入库 | `fb1a6189` | `scripts/generate_grammar_zh.py`，与数据字节级一致 |

### ✅ 9-21 增量（review 收口轮，git log 实证）

> 这轮的起点是一个反常识的事实：review 的 109 个测试全绿，**而主打功能在真实页面里是死的**。

| 事项 | 落地提交 | 说明 |
| --- | --- | --- |
| **criteria builder 真实页面完全失效** | `65227357` | `base.html` 从 `<head>` 加载 JS，而 `#criteria_meta` 由表单局部模板在 `<body>` **稍后**渲染 ⇒ 模块在加载时读到的恒为空、`init()` 在第一道守卫处 bail。改为惰性 `meta()`（miss 不缓存）。**为何没人发现**：node harness stub 了 `getElementById`，看不见加载顺序；集成测试只断言 HTML 含 id、从不执行 JS；预览页恰好把 JSON 放在脚本之前 |
| `Start from` 下拉框不显示预设名 | `8c52e046` | `meta.default_preset` 与 `opt.dataset.criteria` 都是「写了没人读」。改为用序列化结果比对每个 option 的 `dataset.criteria` |
| py3.12 下 `feature/loader.py` pylint 误报 | `7ffdb5f5` | `eps.get()` 在 3.12 上是死代码；照 `parse/registry.py:49` 先例加守卫。CI 一直绿是因为 Lint job 只钉 3.11 |
| **review 浏览器级验收（写 spec）** | `48b40ddd` | `tests/acceptance/review.feature` 三场景：新建 spec 自带默认预设与两行条件；预设被手工改动后选择回落；保存后出现在 dashboard。**负向对照**：换回修复前 JS → 三场景全红 |
| **review 浏览器级验收（会话循环）** | `40b9eb60` | `tests/acceptance/review_session.feature`：建词条 → 搭 spec → 同步入队 → 计数变 1 → 卡片显示词条 → 揭示出现四个 FSRS 评分键 → 评 Good 结束 → Undo 把卡片带回。**负向对照**：注释掉 `start_session()` → 在该断言上失败 |
| **`delete_all_data` 漏删 review 三表** | `6be779e6` | 它号称「Delete everything」却只删 languages/tags/tags2/settings。没有任何东西级联进 `reviewspecs`（无外键）⇒ spec 每次 wipe 都存活；而 `reviewcards` 引用 `words` 且无 `ON DELETE` ⇒ 只要有一张 card，`delete from languages` 直接抛 `FOREIGN KEY constraint failed`，**整个 wipe 会崩**。现 review 三表先删、先于语言级联 |
| 词条筛选标签与行为相反（旧 2.2b） | `7fdc7a74` | 标签改为 "Terms without a parent"，与既有 SQL `parents.parentlist IS NULL` 一致，**查询结果零变更**。id 保留 `filtParentsOnly`（它同时是 localStorage 键）。上游 `upstream/master` 带同一个错标签，非本仓库引入 |
| `meta.default_preset` 冗余清理 | `6b6019a9` | 写了却没人读，正是预设 bug 的成因。删掉并在 `builder_meta` docstring 写明「不发送 JS 不读的键」。`DEFAULT_PRESET_ID` 保留（`default_criteria()` 用） |

**这轮确立的两条做法**（值得固化为惯例）：

1. **负向对照是必需的**——浏览器测试变绿不算数，直到你亲眼看过它变红。把修复前的资产换回去重跑，
   还原后用 `git diff` 证明字节级一致。
2. **验收场景必须能被跑第二遍**——`LuteTestClient` 每个场景前 wipe 库，但「wipe」等于
   「`delete_all_data` 删掉的东西」。任何它漏掉的表都会跨场景、跨运行存活。**加表就要加进
   `delete_all_data`**，否则场景第一次通过、第二次失败。

### ⏳ 关键剩余（按「价值 × 紧急性」重排，详见 §1）

1. **部署还账** —— 所有已完成成果（review、parent 回填、播放器、注册表）都还压在本地；
2. **12 个语法引擎仍不可度量** —— 唯一能让「13 种语言」这句话站得住的工作，v1 以来未动；
3. **离线语音栈（Kokoro + whisper）** —— whisper 是比换 TTS 音色更高一档的解锁；
4. **LLM Provider（语境释义 / 例句 / 语法追问）** —— 2026 阅读类工具最大分水岭；
5. **review 验收与打磨** —— 浏览器级验收**已完成**（§0 的 9-21 增量，4 个场景 + 负向对照）；
   仍缺的是**公开的验收指标与统计闭环**，即下面 P1.4 的 ① ②。

---

## 1. 五阶段路线图总览

| 阶段 | 主题 | 目标产出 | 建议周期 |
| --- | --- | --- | --- |
| **P0 还账** | 部署 + 收口 | 生产追平本地；sw.js 复测通过；小 bug 清零 | 3–5 天 |
| **P1 可度量** | 引擎 + 队列证明 | 12 语法引擎第一份非日语数字；review 达标；parent 挂进开书路径 | 2–3 周 |
| **P2 有声闭环** | 离线语音 + 挖矿 | 本地音视频自动成书；句子卡片带原声；导入前试读预估 | 4–8 周 |
| **P3 AI 差异化** | LLM + 分级读物 | 语境释义 / 例句 / 语法追问；storygen 难度闭环 | 8–12 周 |
| **P4 平台化** | API / 剪藏 / i18n | 开放 REST·MCP；Web Clipper；中文 UI；一键安装 | 持续 |

> 相对 9-20 上午版：原 P0-1（FSRS）与 P1-3（parent 回填）已从表中移除；
> 「review 验收」「部署还账」新进 P0/P1。

---

## 2. 逐项细节

### P0 还账（3–5 天，性价比最高，全部半天量级）

| # | 事项 | 现状证据 | 做法 | 验收 |
| --- | --- | --- | --- | --- |
| 0.1 | **部署积压（升级版）** | 本地 `e4531dab` ahead origin 11；生产仍停在 9-19 之前的 commit；review / parent 回填 / 播放器全部未上线 | push + `deploy.sh` + 确认 `lute.service` active | 生产 = `e4531dab`；curl `/review` 200 |
| 0.2 | ~~`/sw.js` 生产 404~~ **误诊；实为鉴权豁免缺失，已修** | 部署后复测：`/sw.js` **不是 404**——路由在（`app_factory.py:353`）、文件在磁盘（12059 B）。未登录请求返回 `302 → /login?next=/sw.js`，因为多用户鉴权门的豁免名单只有 `/login`、`/static/`、`/favicon.ico`，而 `/sw.js` 和两个 manifest 由**根路径自有路由**提供、不在 `/static/` 下 ⇒ 被误挡 | 把 `/sw.js`、`/manifest.webmanifest`、`/manifest.json` 写进豁免名单（与门自身 docstring「static assets excepted」的意图一致）；加测试断言它们登录前可取，**且真实页面仍被挡** | 登录前 200 + `application/javascript`；负向对照已验证 |
| 0.3 | ~~词条筛选标签反了（一行 bug）~~ **已完成（`7fdc7a74`）** | `/term` 复选框已改为 "Terms without a parent"，与 SQL `parents.parentlist IS NULL` 一致，查询结果零变更；补了两个测试（查询语义 + 渲染标签） | 标签与行为一致 ✅ |
| 0.4 | ~~生产目录残留~~ **已清理（`git status` 已为空）** | `/opt/lute` 原有 `backup/predeploy_20260918_191006/`（350M）、`bulk_import_erin.py`、3 个 `deploy.sh.bak_*`、以及 `lute/static/js/never_cache*` 两个符号链接 | **只归档、未删除** —— 移到仓库外的 `/opt/lute_residue_20260921/`（留在仓库内仍会显示 `??`）。动符号链接前后都 curl 验证过 `/static/js/never_cache/*.js` 仍 200 + immutable（专属路由在服务，符号链接本就冗余） | `git status --porcelain` 为空 ✅ |
| 0.5 | tts dropdown 环境性失败 | 本地 0 红，部分环境失败 | 装依赖或 `skipif` 注明原因 | 任何环境全量 0 红 |

### P1 可度量（2–3 周，最高价值主线）

| # | 事项 | 现状证据 | 做法 | 验收 |
| --- | --- | --- | --- | --- |
| 1.1 | **12 语法引擎泛化测量** | `scripts/measure_grammar_panel.py` 只吃日语；其余 12 种只有「规则在自己例句上命中」级别的证据；生产语料除 en/ja 外几乎为零 | 给测量工具加 `--engine en\|es\|...`；en/ja 直接读生产库，其余从 Gutenberg/Wikipedia 摘要各取 50–200 页（不入库） | 每种语言一份 `/tmp/<lang>_corpus.hex` + 一张对照表 |
| 1.2 | 出第一批数字并处置 | 见上 | 每语言：条目/页、零命中规则清单、单条占比 top10；零命中 ⇒ 删或补 spec，占比 >20% ⇒ 降噪；无法达标 ⇒ UI 标「实验性」 | 条目/页 3–15、单条占比 <10%、零命中 = 0 |
| 1.3 | 语料级回归 | 目前只有 ja 有 | 每语言加断言（条目/页区间 + 零命中 = 0）进 `tests/` | 改引擎会红 |
| 1.4 | **review 验收 + 统计** | 浏览器级验收已补（4 场景，含负向对照）；仍无公开指标 | ① 20 词复习 < 90s 的基准测试；② 复习表现统计（按时复习率、各级遗忘曲线）接入 Stats 页 | 指标页可见；基准通过 |
| 1.5 | **parent 挂进开书路径** | 回填 CLI 已就绪；`save_new_textitem_terms` 是并发批量写（有 UNIQUE 冲突重试循环） | 按 `docs/term-parent-lemma-task.md` 的 P3：只对新书单词条挂 parent，放进冲突重试循环；`ParsedToken` 暴露 per-token lemma（P4） | 新书词条 parent 覆盖率 → 明显高于 3.2% |
| 1.6 | 前端护栏（可选） | 唯一护栏是 `test_static_assets.py`（9 项），无 JS 单测 | 最小 `node:test` 覆盖「点击 vs 拖选」「环清理」这类回归过两次的逻辑 | 回归过两次的逻辑有测试 |

### P2 有声闭环（4–8 周，Song 差异化底座）

| # | 事项 | 现状证据 | 做法 | 验收 |
| --- | --- | --- | --- | --- |
| 2.1 | **离线语音栈：TTSProvider 抽象** | `lute/tts/routes.py` 只有 edge-tts，`VOICE_MAP` 硬跑 | 抽 `TTSProvider`（Edge / Kokoro / Piper），路由层不动，逐语言灰度 | 断网可用；Kokoro 日语/中文发音正常 |
| 2.2 | **whisper 自动生成字幕（更大解锁）** | 本地 mp3/m4b **必须自带 srt** 才能成书（`book/types.py` subtitle 类型） | 接 `faster-whisper` 作为可选 extra（复用 `grammar_engine/install/<extra>` 一键安装机制） | 30 分钟日语播客 → 成书全程 < 10 分钟、无需手工准备字幕 |
| 2.3 | **句子挖矿带原声片段** | 视频/音频书已有 cue 时间轴，但挖出的卡片不带原句声音 | ffmpeg 按 cue 切音频片段，随词导出（对称 `userimage` 图片流程） | 一张带「原句朗读音频」的句子卡片，可直接进复习队列 |
| 2.4 | **导入前试读预估** | `calc_status_distribution(book)` 只接受 `Book` 对象，必须先导入才看得到难度档位 | 拆「吃 raw text + language」的变体，导入页粘贴一段即出 EASY/CHAL/HARD | 粘贴未读日文 1 秒内出档位 |
| 2.5 | **PWA 预缓存版本化** | `sw.js` 预缓存 40 条裸 `/static/js/*.js`，CF immutable 一年 | 预缓存改走 `vstatic` URL | 部署新版本后旧 JS 不滞留 |

### P3 AI 差异化（8–12 周）

| # | 事项 | 现状证据 | 做法 | 验收 |
| --- | --- | --- | --- | --- |
| 3.1 | **可插拔 LLM Provider** | `/api/translate` 是「provider + 内存缓存」形状；`lute/bing/` 是唯一 provider 先例 | OpenAI 兼容 / Ollama / 自建 endpoint 三选一；三个交付点按性价比：①语境释义（结合当前句）②例句生成（i+1 难度 ×3）③**语法追问**（只有 Song 能答：有句级语法判定结果） | 流式、≤2s 首字、LLM 挂掉静默降级到字典链路 |
| 3.2 | **分级读物 2.0（storygen 难度闭环）** | `plugins/lute-storygen` 难度只在 prompt 里，生成完无校验 | 生成 → 导入 → 复用 `new_word_percent` 落在 `NEW_WORD_EASY_PERCENT`~`CHAL_PERCENT` 之外就重写；一键 N4→N3 | 命中目标档比例 > 80% |
| 3.3 | **日语三件套** | Sudachi 分词已带读音字段；上游 #616 竖排挂 5 年 | ①振假名（读音字段已在，剩 UI）②音调（接入公开 pitch 库）③竖排（纯 CSS） | 读页可开关振假名；音调标注可见；竖排渲染正常 |

### P4 平台化（持续）

| # | 事项 | 现状证据 | 做法 | 验收 |
| --- | --- | --- | --- | --- |
| 4.1 | **开放 REST / MCP API** | `lute/dev_api/routes.py` 只是测试用内部端点 | 转正：API key 鉴权 + 只读/读写两组 scope；杀手级场景：Agent 每周一跑 `scripts/measure_grammar_panel.py` → 生成语法小测 → 塞复习队列 | 外部 Agent 可读写词库与复习队列 |
| 4.2 | **Web Clipper 浏览器扩展** | 无 | 一次性把任意网页/付费文章存进 Song 变书；不含 saves、保持自托管 | 一键剪藏 → 出现在书库 |
| 4.3 | **UI 中文本地化（i18n）+ 一键安装包** | 上游 #44 自 2023 未动、#504 自 2024 未动；UI 目前中英混排 | 先 zh-CN + en 两套；pyinstaller 一键包 | 普通用户不碰命令行可跑起来 |
| 4.4 | 社区层（谨慎） | 无 | 分享书单、共享 LLM prompt 模板、导出词表；**不做云端账号体系** | 用户可一键导出/导入书单 |

---

## 3. 度量：怎么知道做对了

| 指标 | 当前 | 目标（3 个月） |
| --- | --- | --- |
| 生产是否追平本地 | 否（落后 ≥22 commit） | 是 |
| 12 语法引擎是否有语料级数字 | 否（只有 ja） | 每种语言条目/页 3–15、零命中 = 0 |
| 本地音频能否无需自带字幕成书 | 否 | 30 分钟播客 < 10 分钟成书 |
| 复习队列是否有统计闭环 | 刚落地、无指标 | 按时复习率、遗忘曲线可见 |
| 新书词条 parent 覆盖率 | 3.2%（257/8119） | ≥ 12%（回填后即达，开书路径持续提升） |
| 查词是否给出贴合本句的释义 | 否（字典首条） | 是（LLM 语境化，断网静默降级） |
| 导入前能否试读预估难度 | 否 | 是（1 秒出档位） |
| AI 生成读物是否落在目标档 | 无校验 | 命中率 > 80% |
| 语法分析结果可否被追问 | 否 | 是 |

---

## 4. 明确的「不做清单」（与 ROADMAP-2026 一致，保持）

| 不做 | 理由 |
| --- | --- |
| Duolingo 式游戏化（连胜、排行榜、宝石） | 奖励 streak 而非 comprehension，与深度阅读定位相冲 |
| 自训 LLM / 微调模型 | 收益远低于接好 provider，自托管硬件承载不起 |
| 实时同传、AI 配音 lipsync | 与「提高目标语输入」目标相悖 |
| 强行合并上游全部改动 | 已领先 539+ commit，只 cherry-pick 净新增 |
| 为 iPhone 重写原生 App | PWA 修好 + 响应式即可，性价比差一个数量级 |

---

## 5. 建议推进顺序

1. **P0 全做**（3–5 天）——尤其 0.1 部署：review / parent 回填 / 播放器这些成果**一天没上线，价值就是零**。
2. **P1.1 → 1.2 → 1.3（先 en）**——泛化测量工具 + 第一份非日语数字，直接回答「12 个引擎是能用还是噪声源」。
3. P1.4 / P1.5 可与 1.3 并行（review 验收 + parent 开书路径）。
4. P2 按 2.2（whisper）→ 2.3（挖矿音频）→ 2.1（Kokoro）→ 2.4（试读预估）的顺序，2.2 是最大解锁。
5. P3 / P4 滚动推进，3.1 的「语法追问」是 Song 独有的护城河，值得最先做透。

## 6. 未验证项（诚实清单）

- 生产实际 HEAD 未复核（本地无法访问 `/opt/lute`）；「落后 ≥22 commit」= 9-19 审计的 ≥12 + 9-20 新增 11 commit 推算。
- 0.5 かぐや姫 生产数据现状未验证（沿用 v2 路线图 0.5，未重开）。
- 14 个 pytest skip 的具体名单未逐条核对（9-20 全量 1185 passed / 14 skipped / 0 failed）。
- ko 与其余 10 种引擎的行数、零命中、误报率仍未知（P1 将首次给出）。
- 11MB 词表 JSON 外置（LFS / 按需下载）未评估，仍为可选项。
