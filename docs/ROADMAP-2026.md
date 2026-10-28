# Song(颂) 2026 未来开发路线图

> 制定日期：2026-09-20 ｜ 基线：`all-features-combine` @ `9bc6ff79`
> 输入：本仓库实际代码现状 + 2026 年语言学习工具的公开趋势与竞品动向。
> 用法：每条建议都标了**趋势依据 / 现状缺口 / 落地挂钩（具体到文件）/ 工作量 / 验收指标**，可直接拆成任务。

---

## 0. 先对齐：现在有什么、外面在发生什么

### 0.1 现状矩阵（✅ 已有 ｜ ⚠️ 半成品 ｜ ❌ 缺口）

| 能力 | 状态 | 代码位置 |
| --- | --- | --- |
| 多格式阅读（文本/EPUB/PDF/漫画 Mokuro/ZIP/CBZ） | ✅ | `lute/book/types.py`（单一 book-type 注册表） |
| 影音字幕同步（YouTube / B 站 / mp3/m4a/m4b / 网易云） | ✅ | `lute/read/routes.py`、`lute/netease/`、共用 player 引擎 |
| 全局 TTS 朗读 / 发音 | ⚠️ **只有云端 edge-tts** | `lute/tts/routes.py`（`VOICE_MAP` 硬跑 + 内存缓存） |
| 自动翻译 | ⚠️ **只有 Google 免费接口** | 同上 `/api/translate` |
| 13 语言语法分析引擎（ja 最成熟） | ⚠️ **只有 ja 用语料验证过** | `lute/read/render/grammar_analysis_*.py` |
| 考试词表掌握度统计（JLPT/HSK/DELE/TOPIK/CEFR） | ✅ | `lute/stats/`（含各语种 vocab JSON） |
| **NEW WORD 三档难度（EASY/CHAL/HARD）+ 书列表筛选** | ✅ **已实现** | `lute/book/stats.py`（阈值单点定义）、`book/datatables.py`、`static/js/book-listing-shared.js` |
| Anki 导出 | ⚠️ **单向、依赖桌面 Anki + AnkiConnect** | `lute/ankiexport/`（含 criteria DSL、field_mapping） |
| **应用内 SRS 复习** | ❌ **最大缺口** | 无对应模块 |
| AI 语境释义 / 例句生成 | ❌ | 无 LLM provider（`lute/bing/` 是唯一的 provider 先例） |
| AI 分级读物生成 | ⚠️ 有插件但独立 | `plugins/lute-storygen`（走 feature registry） |
| 插件/feature 机制 | ✅ | `lute/feature/{registry,loader,locator,installer}.py` |
| 多用户 | ✅（较新） | `lute/multiuser/` |
| 备份/恢复 | ✅ | `lute/backup/` |
| 移动端 / PWA | ❌ **2026-10-14 已移除** —— 浏览器 PWA（sw.js + manifest）整体下线，移动端离线阅读由 Song 安卓 app（Hive 离线缓存）承担；`base.html` 保留 unregister-all 脚本清理老客户端残留 SW | `lute/static/` |
| 开放 API / agent 接入 | ❌ | `lute/dev_api/routes.py` 只是测试用内部端点 |
| UI 国际化 | ❌ | 上游 issue #44 自 2023 挂起至今未做 |

### 0.2 2026 年的五个确定性趋势（附信源）

1. **AI 语境化查词成为默认** —— Readlang / Volpora 一类工具在查词瞬间由 AI 给出「贴合本句」的释义与例句，而非静态词典条目。([explainx.ai 2026 指南](https://www.explainx.ai/blog/ai-for-language-learning-complete-guide-2026)、[Readlang](https://glasp.ai/articles/how-to-learn-a-language-with-ai))
2. **FSRS + Anki 是 2026 的记忆层默认答案**，且「卡片必须来自你真正读到的句子」成为共识，纯粹的词汇频率表被抛弃。([glasp 2026 实测总结](https://glasp.ai/articles/how-to-learn-a-language-with-ai))
3. **本地 / 离线 AI 已经可用**：TTS 侧 Kokoro（82M、Apache-2.0、CPU 上快于实时、含日语与中文音色）成为社区默认；ASR 侧 `faster-whisper` / `whisper.cpp` 让任意音视频可以自行生成字幕。([Self-Hosted TTS 2026 评测](https://offlinetts.com/blog/self-hosted-tts-guide-2026/)、[Laptop Voice AI Roundup](https://lucaberton.com/blog/open-source-voice-ai-laptop-roundup))
4. **AI 生成分级读物（Comprehensible Input）成为标准动作**：按 CEFR 等级生成、并可即时调节难度。([explainx.ai](https://www.explainx.ai/blog/ai-for-language-learning-complete-guide-2026))
5. **学习工具本身正在被 AI Agent 化**：工具开始暴露 API / MCP，让用户自己的 Agent 能读写词库与复习队列。

**上游 Lute 的动向**：低维护节奏，长期挂着的正是 PWA(#590)、i18n(#44)、一键安装包(#504)、词典复制按钮(#641)。这些恰好是分叉项目可以抢先交付的差异化。

---

## 1. 路线图总览

| 阶段 | 主题 | 目标产出 | 建议周期 |
| --- | --- | --- | --- |
| **P0** | 补地基 | 离线语音栈 + 移动端可用 + anker 之外的一条腿站起来 | 4–6 周 |
| **P1** | 成闭环 | 阅读 → 挖矿 → 复习，不出 Song 全部走完 | 6–10 周 |
| **P2** | 差异化 | AI 语境释义 + 覆盖率驱动的可理解输入 | 8–12 周 |
| **P3** | 平台化 | 开放 API / Agent / 插件生态 | 持续 |

---

## 2. P0：补地基（4–6 周）

### P0-1 ✅ 内置 FSRS 复习队列（**最高优先级**）

- **趋势依据**：趋势 2。Song 目前只能往外推 Anki，而 Anki 导出要求用户本机装 Anki + AnkiConnect —— 这是把大多数自托管用户挡在门外的一环。
- **现状缺口**：`lute/ankiexport/service.py` 是完整的「语法引擎 + 字段映射 + 筛选 DSL」，但方向是**往外的**；库里没有一个 review 表。
- **落地挂钩**：
  - 复用 `ankiexport/criteria.py` 的 pyparsing DSL 做「入队筛选规则」（如 `status > 1 and language == "ja"`）—— 已经能解析、能校验，几乎零新增代码。
  - 复用 `SentenceLookup`（`field_mapping.py`）拿到每词的真实例句 ⇒ 卡片天然带上下文，正好符合趋势 2 的「卡片必须来自真句子」。
  - 跑题型：` Recognition / Recall(打字) / Cloze（删除 i+1 句里该词）`。
  - 调度：直接移植 FSRS 参数（19 参数 MS 变体），做成可选，别自己发明。
- **工作量**：M（新表 + migration + 一个 review 页 + 一个 `/review/*` blueprint）。
- **验收**：① 零外部依赖完成每日复习；② Anki 导出仍然可用（只加不断）；③ 20 词复习 < 90 秒。

### P0-2 🔊 离线语音栈：Kokoro TTS + whisper 自动生成字幕

- **趋势依据**：趋势 3。当前 TTS 完全依赖 edge-tts（联网、国内容易被墙/限速），而现在 CPU 上就能跑 Apache-2.0 的 Kokoro。
- **落地挂钩**：
  - `lute/tts/routes.py` 已经是一个「lang → voice」映射 + 缓存 + `/tts/<lang>/<text>` 端点的形状 ⇒ **抽一层 `TTSProvider`（Edge / Kokoro / Piper）**，路由层不动，和现有 `VOICE_MAP` 并存，逐个语言灰度。
  - **更大的解锁：whisper。** 现在一个本地 mp3/m4b **必须自带 srt** 才能成为书（见 `book/types.py` 的 subtitle 类型定义）。接上 `faster-whisper` 后，「任意音频/视频 → 自动生成带时间轴字幕 → 变成可读可点的书」这条链才真正闭合。这条比换 TTS 音色本身价值高一档。
- **工作量**：M–L（whisper 是可选 extra，参考 13 个语法引擎已有的 `grammar_engine/install/<extra>` 一键安装机制）。
- **验收**：① 断网可用；② 30 分钟日语播客 → 成书全程 < 10 分钟、无需手工准备字幕。

### P0-3 📱 ~~修好 PWA / 离线阅读~~（已关闭）

- **2026-10-14 关闭**：PWA 路线放弃（sw.js / manifest 已整体删除，浏览器离线阅读需求由 Song 安卓 app 承担）。

### P0-4 🌐 UI 中文本地化 + 一键安装包（分叉价值）

- **趋势依据**：上游 #44（i18n）自 2023 未动、#504（pyinstaller 一键安装）自 2024 未动。Song 的用户界面文案目前中英混排。
- **工作量**：i18n S–M（可先只做 zh-CN + en 两套）；打包 M。
- **验收**：普通用户可以不碰命令行就把 Song 跑起来。

---

## 3. P1：成闭环（6–10 周）

### P1-1 🎧 句子挖矿闭环（带原声片段）

- **趋势依据**：日语圈主流工作流就是 Yomitan + Anki + 句子音频（[2026 日语工具横评](https://languageapps.info/en/article/japanese-study-tools-that-actually-work/)）。Song 已能在视频里点词，但**挖出来的卡片不带那一句的原声**。
- **落地挂钩**：视频/音频书已有 cue 时间轴 ⇒ 用 ffmpeg 按 cue 切出音频片段，随词导出（打 עם `userimage` 已有的图片流程对称）。
- **工作量**：M。
- **验收**：一张带「原句朗读音频」的句子卡片，可直接进 P0-1 的复习队列。

### P1-2 📊 NEW WORD 三档难度：把已有机制用满（**原提案已撤回**）

> ⚠️ **撤回说明**：本条原写的是「按已知覆盖率挑书 / 难度预测」。**这条路已经做完了** ——
> `lute/book/stats.py` 里 `new_word_percent = (status 0 + status 1) / 全部 unique 词`，
> 阈值 `NEW_WORD_EASY_PERCENT = 10` / `NEW_WORD_CHAL_PERCENT = 20`，三档 EASY / CHAL / HARD
> 由 `get_difficulty_label()` **单点定义**，再由 `difficulty_sql_case()` 与 `difficulty_filter_sql()`
> 供书列表 SQL 和前端徽章共用同一套阈值（不会漂移）；前端是 `static/js/book-listing-shared.js`
> 里可点击的徽章 + `filtNewWord` 筛选，系列页按 `AVG(new_word_percent)` 聚合。
> 采样策略也已经做对：普通书全书均匀取 15 页、漫画取 1000 行、PDF 直接抽页取词，
> 结果缓存进 `bookstats` 并带 `stale` 标记。
> ⇒ 我原本想要的「已知覆盖率」就是 `100 - new_word_percent`，**是本条的重复造轮子，撤回。**

真正没做的只剩「把它接到另外三个场景」，按性价比排序：

- **① 导入前试读预估（最大缺口）**：现在**必须先把书导进来**才看得到档位。
  `calc_status_distribution(book)` 只接受 `Book` 对象 ⇒ 拆出一个「吃 raw text + language」的变体，
  在导入页粘一段文字就实时出 EASY / CHAL / HARD。渲染管线现成、成本低，
  但它决定的是「值不值得花时间读这本」，处在决策链最前端。
- **② 给 storygen 当验收闸门**（见 P2-2）：生成 → 导入 → 算 `new_word_percent` → 不在目标档就重写。
  **不需要任何新指标**，直接复用 `NEW_WORD_EASY_PERCENT` / `NEW_WORD_CHAL_PERCENT`。
- **③ 按语言可配阈值 + 更细粒度（低优先）**：10/20 目前是全局常量，但日语（汉字词、unique 词膨胀）
  与西语的手感并不一样；且现在只有整书一档，没有章/页级热度（「第 5 章突然变 HARD」看不出来）。

- **工作量**：① S–M ｜ ② S ｜ ③ M。
- **验收**：① 粘贴一段从未读过的日文 → 1 秒内出档位；② storygen 产出物命中目标档的比例 > 80%。

### P1-3 🧠 完成词条 parent 回填（**你们已经开了一条线**）

- **现状**：只读探针已跑完 —— ja 8,119 词条里只有 257（3.2%）有 parent；干跑回填上界 1,061 条，但 `sync_status` 要求「恰好 1 个 parent」⇒ 96.8% 开不了。
- **价值**：parent 是「看到 食べさせられた 知道是 食べる」的钥匙，直接关系到 same-family 词的复习打通。
- **工作量**：M（且已避开最大的坑 —— 不用动 `sync_status`）。
- **验收**：回填后直接接入 P0-1 的复习队列，派生词不再重复造卡。

---

## 4. P2：差异化（8–12 周）

### P2-1 🤖 可插拔 LLM Provider：语境释义 / 例句 / 语法追问

- **趋势依据**：趋势 1 + 4，这是 2026 阅读类工具最大的分水岭。
- **落地挂钩**：`/api/translate` 的「provider + 内存缓存」形状可以原样复用；设计成 **OpenAI 兼容 / Ollama / 任何自建 endpoint 三选一**（Ollama 正好满足自托管用户的离线要求）。
- **三个可交付点**（按性价比排序）：
  1. **语境释义**：点词时释义由 LLM 结合**当前句**生成，而不是字典首条；
  2. **例句生成**：给生词造 3 个 i+1 难度的例句（趋势 2 说过，带上下文的卡片保留率显著更高）；
  3. **语法追问**：直接把已经识别出来的语法条（语法面板当前平均 13.6 条/页）当作提问入口 —— 「这个 〜てしまう 在这里是什么用法」，这个问题**只有 Song 能答**，因为只有 Song 有句级语法判定结果。
- **工作量**：M（不含 UI 打磨）。
- **验收**：流式输出、≤2s 出首字、LLM 挂掉时**静默降级**到现有字典链路（这条必须做，否则一次断网就毁掉阅读流）。

### P2-2 📚 AI 分级读物 2.0（把 storygen 插件升级成难度可控）

- **现状**：`plugins/lute-storygen` 已经在跑（按已知/未知词生成故事），但难度只体现在 prompt 里，生成完没有任何校验。
- **升级方向**：**不需要新指标** —— 生成 → 导入 → 复用 P1-2 已有的 `new_word_percent`，
  落在 `NEW_WORD_EASY_PERCENT`~`NEW_WORD_CHAL_PERCENT` 之外就重写；再加「一键调节，从 N4 调到 N3」。
  正好呼应趋势 4 的「动态难度校准」。
- **工作量**：M。

### P2-3 🈶 日语三件套：振假名 / 音调（pitch accent）/ 竖排

- **趋势依据**：这是 Song 相对通用阅读器的护城河，且全是工具类缺口。
- **落地挂钩**：Sudachi 分词结果里已经带了读音字段 ⇒ 振假名几乎是剩 UI；音调接入公开 pitch 库；竖排是纯 CSS**（上游 #616 已辫多年未决）**。
- **工作量**：振假名 S ｜ 音调 M ｜ 竖排 M。

---

## 5. P3：平台化（持续）

### P3-1 🔌 开放 REST / MCP API，让 Agent 驱动 Song

- **趋势依据**：趋势 5。学习工具正在从「人用」变成「人和 Agent 一起用」。
- **落地挂钩**：`lute/dev_api/routes.py` 已经是「内部 JSON 端点」的先例，把它转正 —— 加 API key 鉴权、只读/读写两组 scope。
- **杀手级场景**：你的 Agent 每周一自动跑 `scripts/measure_grammar_panel.py` → 发现这周新欠了 xx 条语法 → 自动生成一份 N3 语法小测 → 塞进复习队列。这条链**只有 Song 能闭环**（因为它同时有语料、有词库、有语法判定、有复习队列）。
- **工作量**：S–M。

### P3-2 🔍 Web Clipper 浏览器扩展

一次性把任意网页/付费文章存进 Song 变成书。**不含 saves**、最好保持自托管定位。

### P3-3 👥 社区层（谨慎）

分享书单、共享 LLM prompt 模板、导出词表。**不要做云端账号体系**——这会背叛自托管用户。

---

## 6. 明确的「不做清单」

| 不做 | 理由 |
| --- | --- |
| Duolingo 式游戏化（连胜、排行榜、宝石） | 2026 的共识是它奖励 streak 而不是 comprehension，与「深度阅读」定位相冲 |
| 自训 LLM / 微调模型 | 收益远低于接一个好 provider，且自托管硬件承载不起 |
| 实时同传、AI 配音[_] lipsync | 与「提高目标语输入」的目标相悖（母语配音会剥夺可理解输入） |
| 强行合并上游全部改动 | 已领先 529 commit，只 cherry-pick 净新增（现有纪律，保持） |
| 为了 iPhone 端重写原生 App | PWA 修好 + 响应式即可，性价比差一个数量级 |

---

## 7. 建议的推进顺序（周计划）

| 周 | 交付 |
| --- | --- |
| W1–2 | **P0-3 PWA**（短平快，立刻解决移动端） + P0-2 whisper 原型验证 feasibility |
| W3–6 | **P0-1 FSRS 复习队列**（复用品类 DSL + SentenceLookup） |
| W7–10 | **P0-2 离线 TTS（Kokoro）+ whisper 字幕落地** |
| W11–14 | **P1-1 句子挖矿音频** + **P1-2 ① 导入前试读预估** |
| W15–18 | **P2-1 LLM Provider（语境释义 + 语法追问）** |
| W19+ | P1-3 parent 回填、P2-2 分级读物 2.0、P2-3 日语三件套、P3-1 开放 API |

---

## 8. 度量：怎么知道做对了

| 指标 | 当前 | 目标（3 个月） |
| --- | --- | --- |
| 每日复习能否不出 Song 完成 | 否 | 是 |
| 本地音频能否无需自带字幕成书 | 否 | 是 |
| 移动端「添加到主屏幕」 | 不可用 | 可用 |
| 查词是否给出贴合本句的释义 | 否（字典首条） | 是（LLM 语境化） |
| 是否能按生词率挑书 | **已达成**（NEW WORD 三档） | 保持 |
| 导入前能否试读预估难度 | 否 | 是 |
| AI 生成读物是否落在目标档 | 无校验 | 命中率 > 80% |
| 语法分析结果可否被追问 | 否 | 是 |

---

## 附：趋势信源

- [AI for Language Learning 2026: Beyond Duolingo — What Actually Works](https://www.explainx.ai/blog/ai-for-language-learning-complete-guide-2026)
- [How to Learn a Language with AI (2026)](https://glasp.ai/articles/how-to-learn-a-language-with-ai)
- [Self-Hosted TTS in 2026: Run Your Own AI Voice Server](https://offlinetts.com/blog/self-hosted-tts-guide-2026/)
- [Open-Source Voice AI You Can Run on a Laptop](https://lucaberton.com/blog/open-source-voice-ai-laptop-roundup)
- [Japanese Study Tools That Actually Work（2026）](https://languageapps.info/en/article/japanese-study-tools-that-actually-work/)
- 上游 Lute issue/PR：#44(i18n)、#504(一键安装)、#590(PWA)、#616(竖排)、#641(词典复制)
