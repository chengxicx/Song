# 任务：把 lemma 接进词条创建路径（parent 回填）

> 新建窗口执行本任务时，先读这份文件 + `.workbuddy/memory/MEMORY.md` 的「词条 parent / lemma」小节。
> 路线图里的编号：`docs/roadmap.md` 的 **2.3**。

## 目标

让日语词条的 parent（活用形 → 词典形）自动化。把覆盖率从现在的 **257 / 8,119（3.2%）** 提到
**≈1,061（13.1%）**，并且**默认不改动任何 `WoStatus`**。

## 背景：为什么现在只有 3.2%

`parent` = `wordparents(WpWoID, WpParentWoID)`，配套 `WoSyncStatus` + 触发器
`trig_wordparents_after_insert_update_parent_WoStatus_if_following`，用于「标一个形态，词典形跟着变」。
现状是三条墙叠出来的：

1. **路径墙**：唯一写 `wordparents` 的地方是 `TermRepository._build_db_term()`
   （`lute/term/model.py`），调用者只有词条表单、批量编辑、`bulk_status_update` 三条**手动**路径。
   开书路径 `calculate_textitems.py::_create_missing_status_0_terms` 用
   `Term.create_term_no_parsing()` 建词条，再由 `read/service.py::save_new_textitem_terms`
   裸 `session.add()` 落库 ⇒ **完全绕开 parent**。
2. **短路墙**：唯一调用 `parser.get_lemma()` 并 `parents=[lemma]` 的是
   `TermRepository.find_or_new()`，而它**第一行 `find()` 命中就 return**；
   开书时词条已全部建好 ⇒ 那段永不执行。
3. **语言墙**：`Parser.get_lemma()` 基类返回 `None`（只 ja（Sudachi/MeCab）与 ko 插件实现）。
   Sudachi 版：**全假名直接 return None**、`lemma == text` 不挂、助詞/助動詞（`_BOUND_POS1`）跳过。

## 已完成的 P1：干跑（只读）

工具：`scripts/probe_term_parents.py`（`mode=ro`）

```
python -m scripts.probe_term_parents --db /tmp/<库副本>.db
```

2026-09-20 在生产库副本上的结果：

| 项 | 数值 |
| --- | --- |
| 日语词条总数 | 8,119 |
| 全假名（lemma 恒为 None，无解） | 1,414（17.4%） |
| **可得 parent** | **1,061（13.1%）**，其中 **543** 条仍是 `status 0` |
| 需新建的 parent 词条 | **337** 个不同文本（最多 368 行） |
| 拼接式 lemma（须过滤） | 48（4.5%），如 `似ている → 似るいる`、`間もなく → 間ない` |
| 现状 parent 覆盖 | 257 条（3.2%），`sync=1` 170 条，**子父状态不一致 0 对** |

多 token 词条里还混着整句中文（`今·天·是·咲·的·生日…`、`第·5···8·課·文法…`），是导入材料的中文被按日语建了词条
⇒ **必须跳过含零宽空格（`\u200B`）的多 token 词条**。

## 范围（按顺序，每步独立可交付）

- **P2 回填 CLI**（主体）：扫描现有 ja 词条 → 用 `get_lemma()` 求 lemma → 写 `wordparents`。
  `WoSyncStatus` **默认 0**。必须幂等、无环、每 child ≤1 parent。
- **P3 开书路径**：只对新书**单词条、非全假名**的词挂 parent，且必须放进
  `save_new_textitem_terms` 的 UNIQUE 冲突重试循环里（首开一本书会并发建同一批词条，docstring 有说明）。
- **P4（可选、更干净）**：给 `ParsedToken` 暴露 per-token lemma，用页面上下文的 lemma，
  替代「对孤立字符串重新分词」（`find_or_new` docstring 明确警告过这个坑）。

## 硬约束（违反即视为失败）

1. **默认 `WoSyncStatus = 0`**：schema 触发器会写 `WoStatus`，那是用户最贵的数据；
   `sync` 只在你逐个确认时才开。
2. **回填不改变任何已有 `WoStatus`**：跑前跑后各做一次状态分布 diff，必须为空。
3. **只挂单词条**：含零宽空格（多 token）的词条、全假名词条、拼接式 lemma 一律跳过。
4. **先在库副本上跑**，生产执行前先备份 `lute_data/users/chengxi/lute.db`。
5. 不动 `lute/db/language_defs` 子模块（本次不需要）。

## 验收标准

- ja parent 覆盖率 **≥12%**；`wordparents` 无环、每 child ≤1 parent。
- `WoStatus` 分布 **diff 为空**（回填前后）。
- 词条总数增量 ≈**337**，且与脚本报告一致（多出来的 parent 词条会出现在词表里，需在交付说明里写清）。
- 测试：为「全假名跳过 / 多 token 跳过 / 拼接式 lemma 过滤 / 幂等 / 无环」加 unit；
  全量 pytest 保持 **1070 passed / 6 failed（既有环境性）/ 1 skipped**，不允许新增失败。
- 生产验证（三处可见）：`/term` 列表的 ParentText 列有值、点词弹窗标题变成 `入りました (入る)` 形态、
  按书筛词表时多出书里没出现过的词典形。

## 环境与流程

- 生产 https://www.metaman.dpdns.org（Basic Auth `chengxi:Baidu1!`），SSH `root@172.236.226.132` 免密。
  代码 `/opt/lute`，venv py3.11，systemd `lute.service`(5001)。**真实库
  `/opt/lute/lute_data/users/chengxi/lute.db`**（根目录 `lute.db` 是 0 字节空壳）。
- 部署链路：本地改 → commit → `git push origin all-features-combine` → `ssh … 'bash /opt/lute/deploy.sh'` → 验证。
- 本地 venv py3.9（`./venv/bin/python`），有 sudachipy + sudachidict_core，`get_lemma` 可离线调用。
- **只读探针配方**：本地写脚本 → `scp` 到服务器 `/tmp` → `cd /opt/lute && ./venv/bin/python /tmp/x.py`；
  SQLite 用 `sqlite3.connect("file:...?mode=ro", uri=True)`。用完清理 `/tmp`。
- ⚠️ **服务器 python 是 3.11 ⇒ f-string 里不能出现反斜杠**（`c["text"]` 之类会 SyntaxError），用 `%` 格式化。
- ⚠️ 生产跑不了 pytest（conftest 要求库名 `test_`），只能跑独立脚本。
- 跑测试前先读 `.workbuddy/memory/platform-notes.md` 的「测试环境铁律」与「测试套件护栏」。

## 参考

- `docs/roadmap.md` 2.3（本条）/ 2.2b（`/term` 筛选标签与行为相反，**独立小修，本次不做**）
- `scripts/probe_term_parents.py`（P1 工具，可直接扩展成 P2 的 `--dry-run`）
- `.workbuddy/memory/2026-09-20.md`：干跑数字、真实父子样例、弹窗/状态联动的实测细节
- `.workbuddy/memory/MEMORY.md`「词条 parent / lemma」小节：不变式与坑

---

## P2 已完成：回填 CLI（2026-09-20 实现 + 副本彩排通过）

> 用户 2026-09-20 03:26 确认「这个语义我接受，按前面说的做吧」⇒ 按下面实施。
> **本地已实现并测试，生产库尚未执行**（生产执行配方见文末「生产执行」）。

### 交付物
| 文件 | 内容 |
| --- | --- |
| `lute/term/lemma_parents.py` | 纯决策逻辑（过滤 / 计划 / lemma 链 / 环检测），不碰 DB |
| `lute/cli/term_parent_backfill.py` | 扫描 → 计划 → 写库 → JSONL 审计 → `undo_backfill` |
| `lute/cli/commands.py` | 注册 `parent_backfill` / `parent_backfill_undo` |
| `lute/parse/sudachi_parser.py` | 新增 `content_token_count()`（拼接式 lemma 判据；刻意不重构 `get_lemma()`） |
| `tests/unit/term/test_lemma_parents.py` · `tests/unit/cli/test_term_parent_backfill.py` | 33 项 |

### 生产库副本实测（2026-09-20 03:3x；快照用 `sqlite3.backup()` 取，非裸拷）
```
Language            : Japanese (id 13)
Terms               : 8119
Have a parent today : 250 (  3.1%)        <- 见下方「与 P1 的差异」
Links               : 727 (  9.0%)  -> coverage  12.0%
Parent terms        : 289 new, 263 already existed
  statuses of the new parent terms: 0:226, 1:31, 99:32
  current statuses of the reused parents: 0:72, 1:124, 3:1, 98:3, 99:63
  statuses of the children that get a parent: 0:496, 1:107, 3:3, 98:3, 99:118
  links that followed a lemma chain (1): 在 -> 在り -> 在る
Not linked          : 7392
  parser returned no dictionary form               5243
  all hiragana: no dictionary form                 1407
  multi-token term (contains a zero-width space)    409
  already has a parent (left alone)                 250
  lemma is the term itself                           78   (LingQMiniStories -> lingqministories)
  lemma is a concatenation of several words           5   (いつの間にか -> いつ間)
```

**与 P1 预算的差异（1,061 / 337 → 727 / 289）**：P1 的 1,061 是「文本能算出 lemma 的行数」，把任务书
硬约束 3 要排掉的东西也算进去了。727 = 1,061 − 250（已有 parent）− 78（lemma 只是大小写差异，其实是同一个词条）
− 5（拼接式）− 其余落在 409 条 zws 里（其中多数 `get_lemma` 返回 None，本就不在 1,061 内）。
覆盖 **250/8119 → 977/8119 = 12.03%**，过 ≥12% 线；`--limit` 会小幅改变分子。
「含零宽空格（多 token）」这条实测只有 **409 条**（不是几千条），所以硬约束 3 的代价可控。

**放宽多 token 策略值不值？不值**：`--multi-token-policy single-content-word`（放行 `入りました→入る`）
只多 **33** 条链接（760 links / 569 parents，覆盖 12.40%），却要偏离硬约束 3 的字面 ⇒ 默认保持 `skip`。

### 副本彩排结果（在 `lute.db` 快照上真跑 `--commit`）
```
terms            : 13338 -> 13627 (+289)
existing changed : 0            <- 硬约束 2：按 WoID 逐一比对 WoStatus + WoSyncStatus
existing deleted : 0
links            : 258 -> 985 (+727 / -0)
children >1 parent (new): 0     <- 存量那条 2-parent 子词（通って→通う+通る）是既有数据，未动
cycles           : 0
new links under sync-1 children: 0
new term statuses: [(0,226), (1,31), (99,32)]
VERDICT: OK
```
- **幂等**：紧接着再跑一次 `--commit` ⇒ `Links written: 0`。
  ⚠️ 首版会多写 1 条，根因是 **lemma 链**：新建的 parent `在り`（来自单词条 `在`）自己又是活用形（`在り→在る`）。
  修法是**在计划阶段就把 lemma 链追到根**（`resolve_lemma_root`，≤3 跳）⇒ 不创建 `在り`、直接 `在→在る`。
- **undo**：`parent_backfill_undo --audit … --commit --delete-created-terms` ⇒ 727 链接 + 289 词条全撤，
  库回到与快照同构（13338 terms / 258 links）。

### 设计决定

### 落点：命令进 `lute/cli`，纯逻辑单独成模块
- `lute/cli/term_parent_backfill.py`（实现）+ `lute/cli/commands.py` 注册 `parent_backfill`，
  与既有 `language_export` / `import_books_from_csv` 同构，走 `--commit` 干跑约定：
  ```
  flask --app lute.app_factory cli parent_backfill --language Japanese          # 干跑（默认）
  flask --app lute.app_factory cli parent_backfill --language Japanese --limit 50 --commit
  ```
  选它而不是 `scripts/*.py`+scp 的三个理由：① **必须跑在 app 上下文里**——`_get_dict_setting/_get_mode_setting`
  从 config 读（默认 `core`/`C`），P1 探针用的是本地默认值，语言若配了别的 dict/mode 结果会不一样；
  ② 现有 CLI 已有「默认干跑、`--commit` 才写」的约定；③ `tests/unit/cli/` 可直接测。
- 纯逻辑（过滤 + 生成计划 + 环检测）放 `lute/term/lemma_parents.py`，**不碰 DB**、可单测。
- `scripts/probe_term_parents.py` 保持原样，不做重构（P1 证据可复现）。

### 写库方式（关键决定）：**不经过 `TermRepository.add()`**
读码确认 `_build_db_term()` 这条路有三处会违反硬约束：
1. `t.status = term.status` 重写子词状态（BO 的 `_status_explicitly_set` 被 setter 置 True）；
2. `_find_or_create_parent()`：parent **已存在但 status 0** 时 `p.status = term.status`
   ⇒ **改已有 `WoStatus`，直接违反硬约束 2**（P1 已知「新建 337」里没有这一项，但存量 status 0 的 parent 会中招）；
3. 附带把子词的 translation / 图片 / 标签复制进 parent，并 `remove_all_parents()` 再重建。

⇒ P2 自己写：
- 新建 parent：`Term.create_term_no_parsing(language, lemma)`（**不再分词** ⇒ 不会插入 zws、`text_lc` 稳定，
  下次查找必命中、天然幂等）；断言行内无 `\u200B`、`status` 显式赋值。
- 建链接：`insert(wordparents).values(WpWoID=..., WpParentWoID=...).prefix_with("OR IGNORE")`
  （表无 PK，只有 `wordparent_pair` 唯一索引）。

**触发器安全性可证明**：`trig_wordparents_after_insert_update_parent_WoStatus_if_following`
的 `AND 1 = (… WHERE WoSyncStatus = 1 AND WoID = new.WpWoID)` 只在**子词 sync=1** 时才写 parent 状态；
回填的 child 一律 sync=0（无 parent ⇒ 从未置 1）⇒ 该触发器不可能改动任何 `WoStatus`。
仍加运行前断言：查到 sync=1 的候选 child 就跳过并计数（0 才算通过）。
`words` 上只有 UPDATE / DELETE 触发器，**新建 parent 词条不会级联任何状态**。

### 过滤规则（沿用 P1 的判据，全部可单测）
| 跳过原因 | 判据 |
| --- | --- |
| `kana_only` | 全平假名（`get_lemma` 本就返回 None，显式跳过并计数） |
| `multi_token` | 词条文本含零宽空格 `\u200B`（顺带排掉被按日语建条的中文整句） |
| `no_lemma` | `get_lemma()` 为 None 或 == 去 zws 后的原文 |
| `concatenated` | 内容 token ≠ 1（P1 的 48 条 `似ている→似るいる`） |
| `already_has_parent` | 子词已有 ≥1 parent（保护现有 257 条手工数据，同时保证每 child ≤1 parent） |
| `self_link` / `cycle` | `parent.text_lc == child.text_lc`；或沿 parent 链上溯 ≤10 层撞到 child |
| `sync_flag_set` | 子词 `WoSyncStatus = 1`（理论为 0，出现即异常） |

### 影响面
| 面 | 变化 |
| --- | --- |
| `words` | **+289 行**新 parent（状态**继承子词**，与 `_find_or_create_parent` 同语义）。**已存在行的 `WoStatus`/`WoSyncStatus` 零改动**（彩排实测 0） |
| `wordparents` | **+727 行**；现有 258 行与 170 对 sync=1 不动 |
| 验收的「分布 diff 为空」 | ⚠️ 必须按 **WoID** 比（回填前已存在的 id 逐一比对），整体直方图因新增行必然变——口径要在 dry-run 报告里写死 |
| 词表 / 统计 | 词条数 8,119 → 8,408（ja）；语言级词表多出书里没出现过的词典形 |
| `/term` 列表 | `ParentText` 列大量有值；反向 bug 的 `filtParentsOnly`（2.2b）命中数下降 |
| 阅读页弹窗 | 标题变 `入りました (入る)`；「子词无释义借 parent 释义」（现仅 3 对）的面扩大；parent 图片共用收益可忽略（全库 7 张） |
| 状态联动 | **不回填 sync** ⇒ 不加新的状态联动；用户之后可在列表里单条点开 |
| 性能 | 无（列表只是 GROUP_CONCAT 多几行、弹窗多 1 行） |

### 「会不会把没见过的词 / 形态变成已知？」——逐条（2026-09-20 追加）

状态码：`0 Unknown / 1–2 New / 3–4 Learning / 5 Learned / 98 Ignored / 99 Well Known`（`statuses` 表）。

| 对象 | 会被改成「已知」吗 | 依据 |
| --- | --- | --- |
| 库里已有的未知词（status 0） | **不会** | 回填只做两件事：新建 parent 行 + 插 `wordparents` 行；已存在行零字节改动 |
| 已存在且 status 0 的 parent 词条 | **保持 0** | 刻意不使用 `_find_or_create_parent()`（那条会 `p.status = term.status`） |
| **新建的 289 个 parent 词条** | **会带状态出生——这是唯一「凭空变已知」的通道**。实测出生状态 `{0:226, 1:31, 99:32}` ⇒ **63 个出生即非未知**（31 个「New(1)」、32 个「Well Known」） | 继承子词状态（同上游语义）：子词 0 ⇒ parent 也是 0；子词非 0 ⇒ parent 一出生就是该状态 |
| 没见过的**活用形**（书里没出现过） | **不创建、不改状态** | 回填只创建 lemma（词典形）；`sync=0` ⇒ 两条状态传播触发器都不走 |
| 现有 258 对连线 / 170 对 sync=1 | 不动 | 只新增行 |
| P3（开书路径，将来） | 新词 `status=0` + `sync=0` ⇒ 子父都仍是未知 | P3 规格 |

**语义**：回填不会把**已有的**未知词变已知；唯一会「凭空变已知」的是它新建的 289 个**词典形**行，
其中 **63 个**会出生即非未知（明细见上表），其余 226 个是未知。子词本身未知时，新 parent 也是未知
（实测：727 条链接里 496 条的 child 是 status 0）。

**干跑报告已含的两个数**：① 新建 parent 的**状态直方图**（按子词状态分解）⇒ 直接给出「会出生即非 0」的行数；
② 若该数不可接受，用 `--new-parent-status zero` 把新 parent 一律建成 0（代价：这些词典形会以「未知词」身份出现在词表里，需手动点掉）。

**⚠️ 两个后门（回填本身不开 sync，但用户一动手就会开）**：
- `/term` 列表的 **ParentText 单元格**：`term/service.py::apply_ajax_update` 里 `if len(term.parents)==1: term.sync_status = True`
  ⇒ 这条例从此**双向**跟随：parent 是 0 ⇒ 子词状态写进 parent；parent 非 0 ⇒ **子词状态被 parent 覆盖**。
  即使只是把那一格原值重新保存一次也会触发。
- **批量编辑** `Set parent (limit one)`：`parent.status != UNKNOWN` ⇒ `term.sync_status = True; term.status = parent.status`。
- P2 不做代码改动，只在交付说明里写明；要不要顺手收紧这两处置位条件，另开一条。

**另有两处非状态的下游可见变化**：Anki 导出字段 `parents` / `terms`（Term and any parents）会带上新 parent；
`filtTermIDs` 让「按书筛词表」多出书里没出现的 parent 行。两者都不涉及 status。

### 待定口径 → 已定
P1 的 **1,061** 是「文本能算出 lemma 的行数」，不是回填后总数；实测校准为 **727 条链接 / 289 个新词条**
（差异来源见上文「与 P1 的差异」）。总覆盖 **12.03%**。

### 生产执行配方（本地已就绪，**待放行**）
> 前置：`git push origin all-features-combine` + `deploy.sh`（CLI 在仓库里，生产没部署就没有这个命令）。
> 生产 venv 是 python 3.11 ⇒ 下面的一行脚本**不用 f-string**（避免反斜杠）。

```bash
# 0. 备份（必做）；用 sqlite backup API，别裸拷运行中的库
ssh root@172.236.226.132 'cd /opt/lute && ./venv/bin/python -c "
import sqlite3, time
s = sqlite3.connect(\"file:/opt/lute/lute_data/users/chengxi/lute.db?mode=ro\", uri=True)
d = sqlite3.connect(\"/opt/lute/backup/pre_parent_backfill_%d.db\" % int(time.time()))
s.backup(d); d.close(); s.close()"'

# 1. 回填前状态快照（硬约束 2 的 diff 用）
ssh root@172.236.226.132 'cd /opt/lute && ./venv/bin/python -c "
import sqlite3
c = sqlite3.connect(\"file:/opt/lute/lute_data/users/chengxi/lute.db?mode=ro\", uri=True)
out = open(\"/tmp/status_before.txt\", \"w\")
for r in c.execute(\"select WoID, WoStatus, WoSyncStatus from words\"):
    out.write(\"%d\t%d\t%s\n\" % r)
out.close()"'

# 2. 干跑（默认不写），确认数字与副本一致
ssh root@172.236.226.132 'cd /opt/lute && ./venv/bin/flask --app lute.app_factory cli parent_backfill --language Japanese'

# 3. 金丝雀 50 条 → 浏览器点几个词看弹窗
ssh root@172.236.226.132 'cd /opt/lute && ./venv/bin/flask --app lute.app_factory cli parent_backfill \
  --language Japanese --limit 50 --commit --audit /opt/lute/backup/parents_canary.jsonl'

# 4. 全量
ssh root@172.236.226.132 'cd /opt/lute && ./venv/bin/flask --app lute.app_factory cli parent_backfill \
  --language Japanese --commit --audit /opt/lute/backup/parents_full.jsonl'

# 5. 校验：after 快照同样导出，然后 comm -23 /tmp/status_before.txt /tmp/status_after.txt 必须为空
#    （= 回填前存在的行没有任何一条的 WoStatus / WoSyncStatus 被改过；新增行是新 id，不影响）
```

**回滚两层**：
1. 主：还原第 0 步的库文件 + `systemctl restart lute`。
2. 副：`parent_backfill_undo --audit <log> --commit [--delete-created-terms]`（审计日志逐条记了
   `(child_id, parent_id)` 与 `parent_created`）。
   ⚠️ 修正此前文档里的一个错误说法：**这库没有 `textitems` 表**（Lute v3 的 TextItem 是渲染期产物，
   词条与书页之间没有外键）⇒ 删除新建词条只级联掉 `wordparents` / `wordtags` / `wordimages` /
   `wordflashmessages`。`undo` 仍保守：词条若已获得其他 parent 链接、标签、图片、flash message
   或用户写的释义，就**只撤链接不删词条**。

### 验收对照
| 验收项 | 实测 |
| --- | --- |
| ja parent 覆盖率 ≥12% | **12.03%**（250 → 977 / 8,119） |
| `wordparents` 无环、每 child ≤1 parent | 彩排 0 环；新增 0 个多 parent 子词（存量 1 条 2-parent 子词是既有数据） |
| `WoStatus` 分布 diff 为空 | 按 WoID 比对：**0 条变化**（新增 289 行不计入存量行） |
| 词条总数增量 ≈337 | **+289**（P1 的 337 是未过滤上界；差异来自硬约束 3 与「已有 parent」guard） |
| 过滤 / 幂等 / 无环 unit | 新增 33 项全绿；全量 pytest 见文末 |
| 生产三处可见 | **未做**（生产尚未执行） |

### 全量 pytest
- 改动落定前的完整一轮：`./venv/bin/python -m pytest -q` ⇒ **6 failed, 1021 passed, 1 skipped**。
  6 个失败与既有环境性失败同源（4 个 `tests/unit/stats` 的 topik/Korean + `test_tts_translate_dropdowns`
  + `tests/integration/test_grammar_analysis.py::test_grammar_analysis_strips_zws_from_client_snippet`）；
  最后一个**单跑通过**（见下），是全量跑的顺序性抖动。
- 格式化/收尾改动后的受影响范围：`tests/unit/term tests/unit/parse tests/unit/cli
  tests/integration/test_grammar_analysis.py` ⇒ **222 passed**。
- ⚠️ 本机沙箱里 21 分钟的全量跑很容易被 SIGTERM 打断（前台超时被杀、后台有时也被杀），
  要复核全量请分块跑或让它在真终端里跑。
- ⚠️ `MEMORY.md` 记的基线 `1070 passed` 与现在（收集数 1233 / 跑出 1021 passed）对不上，
  **基线口径待复核**（可能是当时只跑 `tests/unit` 或不同参数）。没有出现任何**新增的**模块级失败。

### 仍未做（P2 边界外）
- `MeCab`（legacy ja 解析器）没有 `content_token_count()` ⇒ CLI 会**明确报错并拒绝运行**（不是静默降级）。
  生产 ja 用的是 Sudachi，所以不影响本次；将来若要支持 MeCab，补一个同构方法即可。
- 那两个 sync 后门（列表内联编辑 ParentText、批量 `Set parent`）未收紧，只写进了交付说明。

