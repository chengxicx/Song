# MeCab vs Sudachi 评估：能不能删掉 MeCab？

评估日期 2026-09-17 ｜ 对象 `lute/parse/mecab_parser.py` (`JapaneseParser`) 与 `lute/parse/sudachi_parser.py` (`JapaneseSudachiParser`)

## 一句话结论

**Sudachi 确实略优（更快、读音略准），但"完整覆盖"不成立——Sudachi 解析器今天不是线程安全的，而 MeCab 是。删掉 MeCab 会拿掉当前唯一一个并发安全的日语解析器。** 建议：先给 Sudachi 补 per-thread tokenizer（约 6 行，已实测 0 错误），再谈删除。

## 一、现状盘点

| 项 | 事实 |
| --- | --- |
| 生产 `languages.LgParserType` | Japanese = **`japanese_sudachi`**（127 本书 / 8027 词条 / 8519 句） |
| 生产 MeCab 依赖 | `/usr/bin/mecab` + `/usr/lib/x86_64-linux-gnu/libmecab.so.2` + natto-py 1.0.1，`is_supported() = True` |
| 生产 Sudachi 依赖 | SudachiPy 0.6.11 + SudachiDict-core 20260723，`is_supported() = True` |
| `lute/db/language_defs/japanese/definition.yaml` | 仍是 `parser_type: japanese` ⇒ **新建日语语言默认走 MeCab** |
| pyproject | `natto-py>=1.0.1` 在**核心依赖**里；`sudachipy`/`sudachidict_core` 只是可选 extra `japanese-sudachi` |

也就是说：生产已经用 Sudachi 了，但 MeCab 依然是一条活的、完整的代码路径（默认值 + 依赖 + UI + 测试）。

## 二、质量实测

数据源：生产日语语料 16,440 段 / 459,482 字（`texts.TxText` 导出）；读音基准 OpenJLPT 7,237 个带假名读音的词条。

### 速度（整语料，取两次预热后的最小值）

| | 耗时 | 吞吐 |
| --- | --- | --- |
| Sudachi (core / mode C) | 0.79 s | **580,550 chars/s** |
| MeCab (ipadic) | 1.40 s | 328,020 chars/s |

Sudachi 约 **1.8× 快**。

### 分词一致率

| 指标 | 结果 |
| --- | --- |
| token 序列完全一致 | 7,093 / 16,440（**43.1%**） |
| word 子序列完全一致 | 7,673 / 16,440（46.7%） |
| word token 总数 | MeCab 236,821 vs Sudachi 238,306 |
| 去重词数 | MeCab 11,403 vs Sudachi 12,326 |
| 仅 MeCab 产出 | 1,421 |
| 仅 Sudachi 产出 | 2,344 |

43% 的一致率**不等于 Sudachi 更差**——差异是双向的：

- Sudachi 合词更好：`二人`（MeCab 拆成 `二|人`）、`一日`、`いちにち一日`、`うちゅう宇宙`、`ひこう飛行`。
- MeCab 合词更好：`もしも`（Sudachi 拆成 `もし|も`）、`つか捕まえ`（Sudachi 拆成 `つ|か|捕まえ`）。

### 读音准确率（对比 OpenJLPT 读音，hiragana 设置）

| | 正确数 | 准确率 |
| --- | --- | --- |
| Sudachi | 6,672 / 7,237 | **92.2%** |
| MeCab | 6,576 / 7,237 | 90.9% |

各有独占的正确/错误案例，需注意很多是**真正歧义**、而非谁错：

- Sudachi 对、MeCab 错：`一寸→ちょっと`（MeCab いっすん）、`何時でも→いつでも`（なんじでも）、`苛々→いらいら`（MeCab 返回 None）、`画期→かっき`（がき）。
- MeCab 对、Sudachi 错：`一人→いちにん`（Sudachi ひとり，两者都成立，JLPT 取 いちにん）、`玩具→おもちゃ`（Sudachi がんぐ）、`火傷→かしょう`（Sudachi やけど）、`お祖父さん→おじいさん`（Sudachi おそふさん）。

### get_lemma（原形/父词）覆盖

4,000 个真实词条：一致 3,694（92%）；MeCab 独有 196 例能给出原形（`売り→売る`、`錆び→錆びる`、`起こし→起こす`、`考え直し→考え直す`），Sudachi 这些常返回 `None`；Sudachi 独有 142 例（`過ごせ→過ごす` 比 MeCab 的 `過ごせる` 更准）。

**小结**：Sudachi 在速度上明确胜出，读音略准，lemma 覆盖略少。属于"整体略优"，不是"全面覆盖"。

## 三、决定性阻断项：Sudachi 解析器不是线程安全的

`JapaneseSudachiParser._build_tokenizer()` 把**单个** `Tokenizer` 缓存在类属性上，`get_parsed_tokens` / `get_reading` / `get_lemma` 全部共用它，**没有任何锁**。

SudachiPy 0.6.x 是 sudachi.rs 的 PyO3 绑定，底层是可变的 Rust 对象，并发进入会撞 borrow 检查。实测（12 线程同时分词，与生产 waitress 线程数一致）：

| 并发线程 | 调用数 | 报错 | 失败率 |
| --- | --- | --- | --- |
| 2 | 600 | 299 | **49.8%** |
| 4 | 600 | 450 | 75.0% |
| 12 | 600 | 550 | **91.7%** |

错误类型：`RuntimeError: Already borrowed`。

对照 MeCab 解析器——它在 `parse` / `get_reading` / `get_lemma` 三处都套了 `JapaneseParser._mecab_lock`（注释里写得很清楚："natto MeCab wrapper is not thread-safe … the reading page and its subtitle-words AJAX can tokenize concurrently"）：

| | 12 线程错误数 |
| --- | --- |
| MeCab | **0** / 800 |
| Sudachi | 734 / 800 |

生产 `journalctl -u lute --since '30 days ago'` 里 **0 条** `Already borrowed`——目前是单用户低并发，尚未踩到。这是**潜伏 bug**，不是"已经没事"。阅读页 + 字幕 AJAX 并发分词这条路径是真实存在的。

## 四、修复方案（已实测）

三种改法都能把 12 线程失败率降到 0：

| 方案 | 12 线程错误 | 耗时 | 内存 | 评价 |
| --- | --- | --- | --- | --- |
| (a) 共享 `Dictionary` + `threading.local()` 线程内 `Tokenizer` | 0 | 0.09 s | 词典共享 | **推荐** |
| (b) 共享 `Tokenizer` + `threading.Lock` | 0 | 0.03 s | 词典共享 | 最简单，但序列化 |
| (c) 每线程一个 `Dictionary` | 0 | 0.19 s | **×12** | 不推荐 |
| (d) 现状（共享 Tokenizer，无锁） | 550（91.7%） | — | — | 有 bug |

方案 (a) 的形态（`lute/parse/sudachi_parser.py`）：

```python
import threading

class JapaneseSudachiParser(AbstractParser):
    _dict_instance = None          # 共享 Dictionary（词典只加载一次）
    _dict_instance_key = None
    _thread_local = threading.local()   # 每线程一个 Tokenizer
    _build_lock = threading.Lock()

    @classmethod
    def _build_tokenizer(cls, dict_type: str):
        tok = getattr(cls._thread_local, "tokenizer", None)
        if tok is not None and getattr(cls._thread_local, "key", None) == dict_type:
            return tok

        with cls._build_lock:
            if cls._dict_instance is None or cls._dict_instance_key != dict_type:
                from sudachipy import Dictionary
                cls._dict_instance = Dictionary(dict=dict_type)
                cls._dict_instance_key = dict_type
            sd = cls._dict_instance

        tok = sd.create()
        cls._thread_local.tokenizer = tok
        cls._thread_local.key = dict_type
        return tok
```

顺手可修的小 bug：`is_supported()` 用的 cache key 是 `f"{dict_type}|{mode}"`，而 `_build_tokenizer()` 写回的是 `dict_type`，两者**共用同一个 `_instance_key` 属性** ⇒ `is_supported()` 每次都会走慢路径（只是冗余，不会重建）。给二者分配独立属性即可。

## 五、如果仍然要删 MeCab：完整连带面

| # | 位置 | 处理 |
| --- | --- | --- |
| 1 | `lute/parse/mecab_parser.py`（605 行） | 删除 |
| 2 | `lute/parse/registry.py:12,20` | 去掉 import 与 `"japanese"` 条目 |
| 3 | `lute/db/language_defs/japanese/definition.yaml:29` | `parser_type: japanese` → `japanese_sudachi` |
| 4 | **DB 迁移（必需）** | `UPDATE languages SET LgParserType='japanese_sudachi' WHERE LgParserType='japanese'` |
| 5 | `lute/settings/routes.py:23,80-109` | 删 `/settings/test_mecab` |
| 6 | `lute/settings/forms.py:49,55` | 删 `mecab_path`、`japanese_dict` |
| 7 | `lute/templates/settings/form.html:166-229,439-460` | 删 MeCab 表单块 + JS |
| 8 | `lute/static/css/styles.css:2299` | 删 `#mecab_path` 选择器 |
| 9 | `lute/db/management.py:34-65,94,126-132` | 删 `_revised_mecab_path` 与默认 `mecab_path` |
| 10 | `lute/backup/service.py:219,279-299,336,353,393-397` | 去掉恢复时保留 `mecab_path` 的逻辑 |
| 11 | `lute/app_factory.py:675-680` | 删 MeCab 缓存重置 |
| 12 | `pyproject.toml:26` | 删 `natto-py`；**把 `sudachipy`+`sudachidict_core` 从 optional extra 提升为核心依赖**（+约 70MB 词典） |
| 13 | `README.md:50-59`、`README_PyPi.md:12` | 改安装说明 |
| 14 | `docker/Dockerfile:3`、`docker/Dockerfile_scripts/install_everything.sh:6-9`、`docker/docker_hub_overview.md:101` | 去掉 apt mecab |
| 15 | 测试 | `tests/unit/parse/test_JapaneseParser.py` 整篇 MeCab 专属；2 个 characterization 测试要重写；`tests/acceptance/conftest.py:159,190-197` + `unsupported_parser.feature` 用 `"japanese"` 这个 key 做"解析器不可用⇒数据隐藏"的验证，要换成别的 key |
| 16 | `tests/unit/db/test_demo.py:149` | `_restore_japanese_parser` fixture 依赖 `"japanese"` 存在 |

### 最危险的一条

`lute/utils/data_tables.py:supported_parser_type_criteria()` 用 `LgParserType ∈ 已注册且可用解析器` 过滤**书表和词表**。若删掉 MeCab 而库里还有 `LgParserType='japanese'` 的语言：

- 该语言 `is_supported() == False`；
- 其**全部书籍与词条会静默从列表消失**（数据还在，只是被过滤掉，UI 上像"丢了"）；
- Song 支持恢复上游 Lute 的 `.db.gz` 备份，**恢复进来的日语语言就是 `japanese`** ⇒ 这条路会直接踩中。

这条行为正是 acceptance 用例 `unsupported_parser.feature` 在验证的东西。所以第 4 项迁移**不是可选项**。

### 还有一条隐性成本：上游分叉

`lute/parse/registry.py`、`lute/db/language_defs/japanese/definition.yaml`、`README.md`、`docker/*` 都是上游高频改动文件。MeCab 是上游的日语主解析器，删掉之后每一次 cherry-pick 上游 PR 都会在这些文件上产生冲突，需要手工重解。

## 六、建议

1. **短期（推荐）：不删 MeCab。** 先按第四节 (a) 给 Sudachi 加 per-thread tokenizer。这是删 MeCab 的**前置条件**，也顺带修掉一个潜伏的生产 bug。
2. **中期**：若仍要删，按第五节清单执行，务必带第 4 项迁移 SQL，并把 `sudachipy`/`sudachidict_core` 提为核心依赖。
3. **零成本替代方案**：把 MeCab 降级为"备用解析器"——不再作为 `japanese` 语言定义的默认值、不进"Parse as"下拉，但保留代码。这样既不影响上游同步，又保留恢复上游备份时的兜底能力，`backup/service.py` 里那段 `mecab_path` 保留逻辑也不必动。

## 七、实施记录（2026-09-17）

已按建议 1 落地 Sudachi 的线程安全修复。

### 改动

| 文件 | 改动 |
| --- | --- |
| `lute/parse/sudachi_parser.py` | `_build_tokenizer()` 改为「共享 `Dictionary`（加锁，只加载一次）+ `threading.local()` 每线程一个 `Tokenizer`」；`Dictionary` 构造拆到 `_get_dictionary()`，参数名兼容逻辑拆到 `_load_dictionary()`；新增 `_invalidate_cache()`；`is_supported()` 的缓存 key 改用独立属性 `_support_key`（原先与 `_dictionary_key` 共用 `_instance_key`，key 格式不同 ⇒ 每次都走慢路径） |
| `lute/app_factory.py` | DB 恢复后的缓存重置块里，补上 `JapaneseSudachiParser._invalidate_cache()`（原先只重置 MeCab） |
| `tests/unit/parse/test_JapaneseSudachiParser.py` | **新增**（此前该解析器一个测试都没有）：12 个用例，覆盖分词/读音/lemma、每线程独立 tokenizer、跨线程共享 dictionary、8 线程并发分词与并发 reading/lemma、缓存命中、`_invalidate_cache()` |

### 实测（本机 M 系列 Mac，sudachipy 0.6.11 + SudachiDict-core 20260723）

| 场景 | 结果 |
| --- | --- |
| 修复后：8 线程 × 60 次分词 = 480 次调用 | **0 错误**，结果一致 |
| 旧行为（全线程共享一个 Tokenizer） | 第 67 次调用即 `RuntimeError: Already borrowed` |

内存（`RUSAGE_SELF.ru_maxrss` 差值）：进程基线 18.1 MB → 加载 `Dictionary(core)` 74.6 MB（**+56.5 MB**）→ 再建 **12 个 Tokenizer 增量 0.0 MB**。也就是说每线程一个 Tokenizer 的代价约等于零。

### 测试

- `tests/unit/parse`：全绿（含新增 12 例）。
- `tests/unit/{parse,settings,backup,db,language}`：123 passed / 1 failed。唯一失败 `tests/unit/language/test_tts_translate_dropdowns.py::test_form_post_persists_selected_tags` 为**既有失败**——把本次改动全部 stash 掉后同样失败，与本解析器无关。
- `pylint`：改动文件无新增告警（`sudachi_parser.py` 的 `R0914 too-many-locals` 是改动前就有的）。`black`：通过。
- 注：`lute/app_factory.py` 有一处**既有** black 违例（约 183 行，主题 CSS hash 附近），非本次引入，未顺手改动。

### 仍未做

第五节「删 MeCab」清单**未执行**。Sudachi 的线程安全已补齐，理论上不再构成阻断项，但删 MeCab 仍然要面对：`languages.LgParserType='japanese'` 的**必需迁移 SQL**（否则书表/词表按 `supported_parser_type_criteria()` 静默隐藏）、`natto-py` 降级与 `sudachipy` 升格为核心依赖（+56.5 MB）、以及 ~17 处上游高频文件的 cherry-pick 冲突成本。下一步建议是在下面两者中二选一：

- **A. 降级 MeCab 为备用解析器**（不动依赖、不迁库、无上游冲突）——把 `japanese/definition.yaml` 的 `parser_type` 改成 `japanese_sudachi`，并把 `"japanese"` 从「Parse as」下拉里去掉，代码保留。
- **B. 彻底删除 MeCab**——按第五节全清单执行，必须带迁移 SQL 并在生产验证书列表与词表条数不变。

## 八、方案 A 实施记录（2026-09-17，同日续）

已按**方案 A** 落地：MeCab 降级为**备用解析器**——代码、依赖、设置页、`backup/service.py` 的 `mecab_path` 逻辑全部保留，只失去「默认值」和「新语言的选项」两个身份。

### 源码改动

| 文件 | 改动 |
| --- | --- |
| `lute/parse/registry.py` | 新增 `__LUTE_LEGACY_PARSERS__ = {"japanese"}`、`is_legacy_parser()`、`selectable_parsers()`。**关键**：`supported_parser_types()` 继续包含 legacy——书表/词表按它过滤，去掉 legacy 会让老数据的书和词条静默消失 |
| `lute/language/routes.py` | `_dropdown_parser_choices()` 三处 `supported_parsers()` → `selectable_parsers()`；但**当前语言的 `parser_type` 仍强制保留在下拉里**（否则打开老语言编辑页一保存就会静默换解析器） |
| `lute/models/language.py` | `from_dict()` 支持 `parser_type_fallback`：首选解析器不可用时回退。`sudachipy` 是可选 extra、`natto-py` 是核心依赖，所以没有 sudachipy 的机器仍能加载预定义日语语言，而不是让「Japanese」从语言列表里消失 |
| `lute/db/language_defs/japanese/definition.yaml` | `parser_type: japanese` → `japanese_sudachi`，新增 `parser_type_fallback: japanese` |
| `lute/term/model.py` | `find_or_new` 的注释补一句：默认解析器已变，歧义例子随之改变，性质不变 |

**子模块警告**：`lute/db/language_defs` 是 git submodule（`github.com/chengxicx/lute-language-defs`，含 `defs-upstream` = LuteOrg）。definition.yaml 的改动属于**子模块仓库**，必须先在该仓库 commit + push 到 `origin`，再回父仓库 `git add lute/db/language_defs` 更新指针。服务器 `deploy.sh` 带 `set -e` + `git submodule update --init --recursive`，**子模块提交没推上去会让部署直接失败**。

### 测试连带面（全部是「默认解析器变了」的预期结果，不是回归）

| 文件 | 改动 |
| --- | --- |
| `tests/unit/parse/test_registry.py` | +3 例：legacy 仍在 `supported_parser_types()`、不在 `selectable_parsers()`；非 legacy 都仍可选 |
| `tests/unit/language/test_parser_dropdown.py` | **新增** 4 例：新语言无 MeCab；日语语言只给 Sudachi；**老 MeCab 语言仍能看到自己的当前值**；土耳其语言看不到日语解析器 |
| `tests/unit/models/test_Language.py` | +5 例：`parser_type_fallback` 命中/未命中/无 fallback/fallback 也不可用，以及真实日语定义文件的端到端断言 |
| `tests/orm/test_Term.py` | `それはそれで`：3 → **4** token（`それ␣は␣それ␣で`） |
| `tests/unit/term/test_Repository.py` | 歧义短语 `集めれ` → `もしも`（Sudachi 下 `集めれ` 不分词，`もしも` 才是 `もし/も`） |
| `tests/features/rendering.feature` | 日语多词场景：`ヲ/ウメニ`→`ヲウメニ`、`困/寿`→`困寿`。`な/がち` 仍是两个 token，所以「多词术语落在句尾」的覆盖保住 |
| `tests/acceptance/book.feature` | `最初/はね/難しい/。` → `最初/は/ね/難しい/。` |
| `tests/acceptance/unsupported_parser.feature` + `conftest.py` | 禁用 key 从 `japanese` 改为**同时禁用** `japanese_sudachi` 与 `japanese`（demo 日语语言现在来自定义文件 ⇒ 是 Sudachi） |

**值得注意的是**：`tests/features/rendering.feature` 的日语场景几乎全部原样通过（`私は元気です`、`元気です` 多词、`している`（Sudachi 拆成 `し/て/いる` 仍被多词术语匹配成一个 span）、`1234おれの方が強い。`），整篇只有 1 处 token 边界差异。**说明 Sudachi 在渲染层与 MeCab 的差异比 43% 的「分词序列一致率」暗示的小得多**——真正被渲染断言盖到的句子大多是短句。

### 验证

- **L1 全量** `./venv/bin/python -m pytest`：**993 passed / 1 failed / 1 skipped**。唯一失败 `tests/unit/language/test_tts_translate_dropdowns.py::test_form_post_persists_selected_tags` 为**既有失败**（改动 stash 掉后同样失败）。
- **L2 浏览器套件**（串行）：`inv accept` **55 passed / 0 failed**（181.7s）、`inv acceptmobile` **4 passed**（11.8s）、`inv playwright` **3 passed / 1 skipped**（20.6s）——**与记录基线逐项一致**。
- **生产影响面**：只读查过生产库，17 个语言里**没有任何一个的 `LgParserType` 是 `japanese`**（日语早就是 `japanese_sudachi`，127 本 / 8035 词条）。所以方案 A **对现有生产数据零影响、不需要迁移 SQL**，只改变「新建日语语言」的默认解析器。
- `pylint` / `black`：改动文件无新增告警（`sudachi_parser.py` 的 `R0914` 为既有）。

### 环境坑（跑 L2 必踩，两条叠加）

1. 沙箱导出了 `HTTP_PROXY/HTTPS_PROXY`，而 `_site_is_running()` 用 `requests` 探测 `localhost:5001` 会**走代理拿到 502**，且它不是返回 False 而是**直接抛 `RuntimeError: Got code 502`**。必须去掉代理变量（或设 `NO_PROXY=localhost,127.0.0.1`）。
2. `tasks.py` 里起站点的命令是**裸 `python`**（`["python", "-m", "tests.acceptance.start_acceptance_app", port]`），在沙箱里会解析成 WorkBuddy 的 Python 3.13（没有 lute 依赖）⇒ 站点起不来，报 `Exception: Site didn't start?`。而且 `print_subproc_output` 线程是在 `_wait_for_running_site()` **之后**才启动的，站点的崩溃输出根本不会打印——排查时极易误判。需要把 `venv/bin` 放到 PATH 最前。
3. `~/Library/Caches/ms-playwright/` 为空（没下载自带 chromium）⇒ 浏览器启动失败，**整套陪葬**（53 failed in 11s）。用系统 Edge：`LUTE_TEST_BROWSER_CHANNEL=msedge`。

可用命令：

```bash
cd /Users/cxi/Documents/lutedev/lute-v3
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
    NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
    PATH="$PWD/venv/bin:$PATH" \
    LUTE_TEST_BROWSER_CHANNEL=msedge LUTE_TEST_BROWSER_ARGS=--no-proxy-server \
    ./venv/bin/python -m invoke accept
```

这三条**早就写在**现成文档里了：`docs/high-risk-smoke-checklist.md:108-129`（三行 `export` + 串行铁律）与项目 skill `run-lute-browser-tests` 的「必需环境变量」一节。这次绕了远路，唯一原因是**没先加载那个 skill** —— 下次改高危区、要跑 L2 时第一步就把它读出来。

---

## 九、提交与部署（2026-09-17）

### 提交

工作区里其实是**两组独立改动**，分成两个 commit：

| commit | 内容 | 文件 |
| --- | --- | --- |
| `b6e998b0` | `fix(parse): make the Sudachi parser thread-safe` | `lute/parse/sudachi_parser.py`、`lute/app_factory.py`、`tests/unit/parse/test_JapaneseSudachiParser.py`（新增） |
| `900b3088` | `feat(language): demote MeCab to a fallback Japanese parser` | registry / routes / models.language / term.model + 7 个测试文件 + 本报告 + 子模块指针 |

**子模块按前置顺序处理**：先在 `lute/db/language_defs` 里 commit（`7c6af0d`）并 `git push origin master`（`f68c5bd..7c6af0d`），再回父仓库 `git add lute/db/language_defs` 更新指针（`f68c5bd7` → `7c6af0d4`），最后 push 父仓库。部署日志确认了 `Submodule path 'lute/db/language_defs': checked out '7c6af0d4…'`。

### 一个 flake（不是回归）

第一次 L2 重跑出现 `inv accept` **1 failed / 54 passed**，失败项是 `test_book.py::test_bad_text_files_are_rejected[non_utf_8.txt-non_utf_8.txt is not utf-8 encoding]`。

- 单独重跑该用例（6 个参数化全跑）：**6 passed**。
- 紧接着再跑一次全量：**55 passed / 0 failed**。
- **根因**：该 scenario 的 step 是 `make_book_from_file()` —— 点完 `#save` 只 `_sleep(0.2)` 就断言 `page.content()`，**没有等导航提交**。机器忙时 0.2 秒不够，`content()` 拿到的还是**上一页**（失败报文里的 `<title>New Book…` 正是新书表单页）。属既有的 test-side 时序 hack。
- 本地没有装 `pytest-randomly`（`pip list` 只有 `pytest 8.4.2` + `pytest-bdd 7.3.0`）⇒ 顺序是确定的，所以这不是随机排序问题，纯粹是时序竞争。
- 项目里早有同类记录：`.workbuddy/memory/2026-09-13.md`「连续两轮全量 accept 各挂 1 条且**每次都不同**，隔离重跑均通过，第三轮 55/0」；`docs/high-risk-smoke-checklist.md:37` 也标了 `test_i_can_import_a_text_file` 为 flaky。

> **可选的后续改进**（本次未做，因为它出现在部署验证之后、再改就要重跑 L2）：把 `make_book_from_file` / `make_book_from_url` 里的 `_sleep(0.2)` 换成等待导航 —— 例如 `with page.expect_navigation(): page.locator("#save").click(force=True)`。这能从根上消掉这一整类 flake。

### 部署

`ssh root@172.236.226.132 'bash /opt/lute/deploy.sh'` 一次通过：
`b9e14866..900b3088` → `HEAD is now at 900b3088` → 子模块 `7c6af0d4` → `pip install -e .` 无错 → `Active: active (running)`。
重启后的启动日志里 **`Japanese (MeCab)` 与 `Japanese (Sudachi)` 两个解析器都仍在 Enabled parsers 列表**（正是方案 A 的意图），且 `journalctl` 中无任何 error/traceback/exception。

### 生产验证（两层）

**(1) 生产解释器内的行为脚本**（`scp` 到 `/tmp`，用 `./venv/bin/python` 跑，跑完删除）—— 全部 PASS：

- `is_legacy_parser("japanese")` = True；`supported_parser_types()` = `['classicalchinese','japanese','japanese_sudachi','spacedel','turkish']`（**仍含 legacy**）；`selectable_parsers()` = 同上去掉 `japanese`；且严格等于「supported 减 legacy」。
- 生产磁盘上的 `japanese/definition.yaml`：`parser_type: japanese_sudachi` + `parser_type_fallback: japanese`；`Language.from_dict()` 解析为 `japanese_sudachi`；把 `is_supported` monkeypatch 成「sudachi 不存在」后，解析结果回退为 `japanese` ⇒ **fallback 真的生效**。
- **线程压测：8 线程 × 30 轮 × 4 句 = 960 次并发解析，0 异常；每线程 token 数完全相同（`1470` × 8）** —— 这就是线程安全修复在生产解释器上的直接证明（修复前第 67 次调用即 `Already borrowed`）。
- `japanese_sudachi` 与 `japanese` 两个解析器都仍 `is_supported`。

**(2) 生产端到端 UI（Playwright 打 https://www.metaman.dpdns.org）** —— 全部 PASS：

| 检查 | 结果 |
| --- | --- |
| 应用登录 | 通过 |
| `/language/new/Japanese` 的 `select#parser_type` 选项 | `['japanese_sudachi']` — 有 Sudachi、**没有 MeCab** |
| `/language/edit/13`（日语，`LgParserType=japanese_sudachi`） | 选中 `japanese_sudachi`，**不含** MeCab 选项 |
| `/read/285`（日语书） | `luteStartReadingDone === true`，渲染出 **224 个 `span.textitem`** |
| 点术语弹窗 | `作` → 弹窗 `'作\n\nさく'`（解析 + 查词端到端可用） |

UI 脚本本身踩的两个坑（与改动无关，但会伪装成「功能坏了」）：

- **术语弹窗是 jquery-ui tooltip 且挂到 `<body>` 下** ⇒ `#thetext .ui-tooltip` 永远匹配不到，要用全局 `.ui-tooltip`。
- 沙箱有 `HTTP_PROXY` 才能出网，但 Chromium 不会自动继承 ⇒ 访问**外部站点**要 `new_context(proxy={"server": os.environ["HTTP_PROXY"]})`；同时 `launch(args=["--no-proxy-server"])` 免得打 localhost 被代理截。两者不矛盾：launch 关全局代理，context 只给这一个上下文指代理。

这两条已补进 skill `song-lute-deploy` 的 §5b（v1.4.0）；子模块部署前置也补成了新的 §1a。

### 终态

生产 HEAD `900b3088`（`git describe` → `3.10.3.5-411-g900b3088`），子模块 `7c6af0d4`，`lute.service` active。服务器上的临时验证脚本已删除。

