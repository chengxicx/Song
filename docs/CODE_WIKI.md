# Lute v3 Code Wiki

> 版本: 3.10.3.8 | 基于 LuteOrg/lute-v3 分支，集成了 TTS 语音合成、自动翻译等增强功能

---

## 目录

1. [项目概述](#1-项目概述)
2. [技术架构](#2-技术架构)
3. [项目目录结构](#3-项目目录结构)
4. [核心模块详解](#4-核心模块详解)
   - [4.1 应用入口与工厂](#41-应用入口与工厂)
   - [4.2 数据模型层](#42-数据模型层)
   - [4.3 阅读模块](#43-阅读模块)
   - [4.4 分词解析模块](#44-分词解析模块)
   - [4.5 图书管理模块](#45-图书管理模块)
   - [4.6 术语管理模块](#46-术语管理模块)
   - [4.7 数据库模块](#47-数据库模块)
   - [4.8 语言管理模块](#48-语言管理模块)
   - [4.9 设置模块](#49-设置模块)
   - [4.10 TTS 语音合成与翻译](#410-tts-语音合成与翻译)
   - [4.11 Anki 导出模块](#411-anki-导出模块)
   - [4.12 备份恢复模块](#412-备份恢复模块)
   - [4.13 前端模块](#413-前端模块)
   - [4.14 其他辅助模块](#414-其他辅助模块)
5. [数据模型](#5-数据模型)
6. [路由总览](#6-路由总览)
7. [依赖关系](#7-依赖关系)
8. [项目运行方式](#8-项目运行方式)

---

## 1. 项目概述

**Lute** (Learning Using Texts) 是一个**自托管的语言学习阅读平台**，帮助用户通过阅读原文来学习外语。其核心理念是：

- 导入外语文本（书籍、网页、YouTube/Bilibili 视频、MP3 音频、漫画等）
- 对文本进行分词，识别每个单词/术语
- 用户通过点击单词查看翻译、读音、图片，并标记掌握状态
- 支持 60+ 种语言的解析器
- 支持 TTS 语音朗读、自动翻译、Anki 卡片导出、阅读统计等

本分支在原始 Lute v3 基础上增加了 TTS 语音合成 (Edge-TTS)、自动翻译 (Google/MyMemory)、备份恢复 UI、语言冻结/激活、拖拽式列宽调整、Bilibili 视频导入与 DASH 流播放等功能。

---

## 2. 技术架构

| 层级 | 技术栈 |
|------|--------|
| **后端框架** | Flask 3.x (Python 3.8+) |
| **ORM** | Flask-SQLAlchemy 3.x |
| **数据库** | SQLite |
| **模板引擎** | Jinja2 |
| **前端** | jQuery + DataTables + jQuery UI + Tagify |
| **WSGI 服务器** | Waitress (生产) / Werkzeug (开发) |
| **分词引擎** | MeCab (日语), Sudachi (日语), Kiwi (韩语), 空格分隔 (多数语言) |
| **语音合成** | Edge-TTS (Microsoft) |
| **翻译 API** | Google Translate + MyMemory (回退) |
| **构建工具** | Flit (pyproject.toml) |
| **测试** | pytest + behave (BDD) + Playwright |

### 架构图

```
┌──────────────────────────────────────────────────────────────┐
│                       用户浏览器 (Web UI)                     │
│       jQuery + DataTables + Tagify + 自定义 JS 模块         │
└─────────────────────────┬────────────────────────────────────┘
                          │ HTTP / AJAX
┌─────────────────────────▼────────────────────────────────────┐
│                    Flask 应用 (app_factory)                   │
│   ┌─────────┐ ┌──────────┐ ┌────────┐ ┌──────────────────┐  │
│   │  Blueprint 注册  │ 请求/响应钩子 │ 模板注入  │  错误处理     │  │
│   └─────────┘ └──────────┘ └────────┘ └──────────────────┘  │
└─────────────────────────┬────────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────┐
│                    核心业务模块 (Blueprints)                   │
│  ┌───────┐ ┌────────┐ ┌──────┐ ┌──────┐ ┌───────┐ ┌──────┐  │
│  │ 阅读  │ │ 图书   │ │ 术语  │ │ 语言  │ │ TTS   │ │ 备份  │  │
│  │ /read │ │ /book  │ │ /term│ │ /lang │ │ /tts  │ │/backup│  │
│  └───┬───┘ └───┬────┘ └──┬───┘ └──┬───┘ └───┬───┘ └───┬───┘  │
│      │         │         │        │         │         │       │
│  ┌───▼─────────▼─────────▼────────▼─────────▼─────────▼───┐  │
│  │                    Service 层                             │  │
│  └──────────────────────────┬───────────────────────────────┘  │
└─────────────────────────────┼─────────────────────────────────┘
                              │
┌─────────────────────────────▼─────────────────────────────────┐
│     数据模型层 (SQLAlchemy ORM) + Repository 模式              │
│  Book / Text / Term / Language / Setting / Sentence / ...    │
└─────────────────────────────┬─────────────────────────────────┘
                              │
┌─────────────────────────────▼─────────────────────────────────┐
│                    SQLite 数据库文件                            │
│   (lute.db / 用户自定义路径)                                   │
└───────────────────────────────────────────────────────────────┘
```

---

## 3. 项目目录结构

```
lute-v3/
├── lute/                          # 主应用包
│   ├── __init__.py                # 版本信息 (__version__)
│   ├── main.py                    # 生产入口 (waitress serve)
│   ├── app_factory.py             # Flask 应用工厂 (核心)
│   ├── config/                    # 配置模块
│   │   ├── app_config.py          # AppConfig 类，YAML 配置加载
│   │   ├── config.yml.example     # 示例配置
│   │   ├── config.yml.prod        # 生产配置模板
│   │   └── config.yml.docker      # Docker 配置
│   ├── models/                    # SQLAlchemy 数据模型
│   │   ├── book.py                # Book, Text, Sentence, BookStats 等
│   │   ├── term.py                # Term, TermTag, TermImage, Status
│   │   ├── language.py            # Language, LanguageDictionary
│   │   ├── setting.py             # UserSetting, SystemSetting, BackupSettings
│   │   ├── srsexport.py           # SRSExportSpec
│   │   └── repositories.py        # Repository 模式封装
│   ├── db/                        # 数据库管理
│   │   ├── __init__.py            # SQLAlchemy 单例
│   │   ├── setup/main.py          # 数据库初始化 + 迁移管理
│   │   ├── setup/migrator.py      # SqliteMigrator (SQL 迁移引擎)
│   │   ├── demo.py                # 演示数据加载
│   │   ├── management.py          # 数据管理 (删除、默认设置)
│   │   ├── data_cleanup.py        # 数据清理
│   │   └── schema/                # SQL 迁移脚本
│   │       ├── baseline.sql       # 数据库基线
│   │       ├── migrations/        # 单向迁移 (50+ 个 SQL 文件)
│   │       └── migrations_repeatable/  # 可重复迁移
│   ├── read/                      # 阅读模块 (核心)
│   │   ├── routes.py              # 阅读相关路由
│   │   ├── service.py             # 阅读服务 (TermPopup, Service)
│   │   ├── bilibili_stream.py     # Bilibili DASH 流代理 (view/playurl → MPD → 代理)
│   │   ├── forms.py               # 阅读表单
│   │   └── render/                # 文本渲染引擎
│   │       ├── service.py         # RenderService
│   │       ├── text_item.py       # TextItem 数据结构
│   │       ├── calculate_textitems.py  # 文本项计算算法
│   │       └── multiword_indexer.py    # 多词索引 (ahocorapy)
│   ├── parse/                     # 分词解析模块
│   │   ├── base.py                # ParsedToken, AbstractParser 抽象基类
│   │   ├── registry.py            # 解析器注册表 + 插件机制
│   │   ├── space_delimited_parser.py  # 空格分隔语言解析器
│   │   ├── mecab_parser.py        # 日语 MeCab 解析器
│   │   ├── sudachi_parser.py      # 日语 Sudachi 解析器
│   │   ├── kiwi_parser.py         # 韩语 Kiwi 解析器
│   │   └── character_parser.py    # 汉字逐字解析器 (文言文)
│   ├── book/                      # 图书管理模块
│   │   ├── routes.py              # 图书 CRUD + 导入路由
│   │   ├── service.py             # 图书导入服务 (FileTextExtraction)
│   │   ├── model.py               # Book 业务对象 + Repository
│   │   ├── stats.py               # 图书统计 Service
│   │   ├── forms.py               # 图书表单
│   │   ├── datatables.py          # DataTables 数据源
│   │   └── japanese_srt.py        # 日语字幕重分割
│   ├── term/                      # 术语管理模块
│   │   ├── routes.py              # 术语 CRUD 路由
│   │   ├── model.py               # Term 业务对象 + Repository + ReferencesRepository
│   │   ├── service.py             # 术语 Service
│   │   ├── forms.py               # 术语表单
│   │   └── datatables.py          # 术语 DataTables
│   ├── language/                  # 语言管理模块
│   │   ├── routes.py              # 语言 CRUD 路由
│   │   ├── service.py             # 语言 Service (预定义语言加载)
│   │   └── forms.py               # 语言表单
│   ├── settings/                  # 用户设置模块
│   │   ├── current.py             # 全局设置缓存 (current_settings)
│   │   ├── routes.py              # 设置路由
│   │   ├── forms.py               # 设置表单
│   │   └── hotkey_data.py         # 快捷键默认值
│   ├── tts/                       # TTS 语音合成
│   │   └── routes.py              # /tts/ 和 /api/translate/ 路由
│   ├── ankiexport/                # Anki 导出
│   │   ├── routes.py              # 导出路由
│   │   ├── service.py             # 导出 Service
│   │   ├── criteria.py            # 筛选器解析
│   │   ├── field_mapping.py       # 字段映射
│   │   ├── forms.py               # 导出表单
│   │   └── exceptions.py          # 异常定义
│   ├── backup/                    # 备份恢复
│   │   ├── routes.py              # 备份路由
│   │   └── service.py             # 备份 Service
│   ├── themes/                    # 主题管理
│   │   ├── routes.py              # 主题路由
│   │   ├── service.py             # 主题 Service
│   │   └── css/                   # 内置主题 CSS
│   ├── stats/                     # 阅读统计
│   │   ├── routes.py              # 统计路由
│   │   └── service.py             # 统计 Service
│   ├── bookmarks/                 # 书签
│   │   ├── routes.py
│   │   └── datatables.py
│   ├── bing/                      # Bing 图片搜索
│   │   └── routes.py
│   ├── cli/                       # CLI 命令行工具
│   │   ├── commands.py            # Flask CLI 命令
│   │   ├── import_books.py        # CSV 批量导入
│   │   └── language_term_export.py # 语言/书目术语导出
│   ├── term_parent_map/           # 术语父子关系
│   │   ├── routes.py
│   │   └── service.py
│   ├── termimport/                # 术语批量导入
│   │   ├── routes.py
│   │   └── service.py
│   ├── termtag/                   # 术语标签管理
│   │   ├── routes.py
│   │   ├── forms.py
│   │   └── datatables.py
│   ├── userimage/                 # 用户图片服务
│   │   └── routes.py
│   ├── useraudio/                 # 用户音频服务
│   │   └── routes.py
│   ├── dev_api/                   # 开发 API (仅测试)
│   │   └── routes.py
│   ├── utils/                     # 工具模块
│   │   ├── data_tables.py         # DataTables SQL 查询构建器
│   │   ├── formutils.py           # 表单工具函数
│   │   └── debug_helpers.py       # 调试辅助
│   ├── templates/                 # Jinja2 模板
│   │   ├── base.html              # 基础布局
│   │   ├── index.html             # 首页
│   │   └── read/ book/ term/ ...  # 各模块模板
│   └── static/                    # 静态资源
│       ├── js/                    # JS 模块
│       ├── css/                   # 样式
│       ├── icn/                   # 图标
│       └── vendor/                # 第三方库
├── tests/                         # 测试套件
│   ├── unit/                      # 单元测试
│   ├── integration/               # 集成测试
│   ├── acceptance/                # BDD 验收测试 (Playwright)
│   ├── orm/                       # ORM 模型测试
│   └── features/                  # BDD 特征文件
├── devstart.py                    # 开发服务器入口
├── tasks.py                       # Invoke 任务 (lint/test/start)
├── pyproject.toml                 # 项目配置 + 依赖
├── Dockerfile                     # Docker 部署
└── docker/                        # Docker 辅助脚本
```

---

## 4. 核心模块详解

### 4.1 应用入口与工厂

#### [lute/main.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/main.py)
**生产入口**，使用 `waitress` WSGI 服务器。

| 函数 | 描述 |
|------|------|
| `start()` | 主入口，解析命令行参数 (`--port`, `--local`, `--config`) |
| `_start(args)` | 配置并启动应用：加载配置 → 创建应用 → 初始化数据 → 启动服务 |
| `_get_config_file_path()` | 确定配置文件路径（优先级：指定 > 根目录 config.yml > 默认） |
| `_create_prod_config_if_needed()` | 如果 config.yml 不存在，从模板复制 |

#### [lute/app_factory.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/app_factory.py)
**Flask 应用工厂**，是整个应用的组装中心。

| 函数/类 | 描述 |
|---------|------|
| `create_app()` | 主工厂函数：加载配置 → 创建目录 → 初始化数据库 → 创建应用实例 → 初始化解析器插件 |
| `data_initialization()` | 启动后数据初始化：检查并加载演示数据 → 清理数据 |
| `_create_app()` | 创建 Flask 实例，配置 SQLAlchemy，注册所有 Blueprint 和路由 |
| `_add_base_routes()` | 注册基础路由（首页、刷新统计、清除演示数据、版本、错误处理等） |
| `_setup_app_dirs()` | 创建数据目录结构（备份、用户图片、音频、主题、临时目录等） |
| `_init_parser_plugins()` | 加载解析器插件，为每个插件创建数据目录 |

**注册的 Blueprint 列表：**

```python
language_bp, anki_bp, book_bp, bookmarks_bp, term_bp, termtag_bp,
read_bp, bing_bp, userimage_bp, useraudio_bp, termimport_bp,
backup_bp, settings_bp, themes_bp, stats_bp, cli_bp, tts_bp, dev_api_bp
```

**关键中间件：**
- `_redirect_credentialed_url()`: 重定向带 BasicAuth 的 URL 到干净 URL（解决子资源加载问题）
- `_before_request()`: 备份恢复后重置引擎和设置
- `_no_cache_dynamic_pages()`: 禁止缓存 `/read/` 和 `/term/` 下的动态页面

#### [lute/config/app_config.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/config/app_config.py)

| 属性 | 描述 |
|------|------|
| `env` | 运行环境 (`prod` / `dev`) |
| `is_docker` | 是否 Docker 环境 |
| `dbname` | 数据库文件名 |
| `datapath` | 用户数据目录 |
| `dbfilename` | 数据库文件完整路径 |
| `plugin_datapath` | 插件数据目录 |
| `userimagespath` | 用户图片目录 |
| `useraudiopath` | 用户音频目录 |
| `userthemespath` | 用户主题目录 |
| `temppath` | 临时文件目录 |
| `system_backup_path` | 系统自动备份目录 |
| `default_user_backup_path` | 用户默认备份路径 |

---

### 4.2 数据模型层

#### [lute/models/book.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/models/book.py)

**Book** (`books` 表) — 图书实体

| 列 | 类型 | 描述 |
|----|------|------|
| `BkID` | SmallInteger PK | 主键 |
| `BkTitle` | String(200) | 书名 |
| `BkLgID` | Integer FK → `languages.LgID` | 语言 |
| `BkSourceURI` | String(1000) | 来源 URL |
| `BkCurrentTxID` | Integer | 当前阅读的 Text 页面 ID |
| `BkArchived` | Boolean | 是否归档 |
| `BkAudioFilename` | String | 音频文件名 |
| `BkAudioCurrentPos` | Float | 音频当前播放位置 |
| `BkAudioBookmarks` | String | 音频书签 JSON |
| `BkBookType` | String | 书籍类型 (`""`/`"youtube"`/`"bilibili"`/`"manga"`/`"mp3"`) |
| `BkSrtData` | String | 字幕数据 JSON |
| `BkVideoCurrentPos` | Float | 视频当前播放位置 |
| `BkMangaPath` | String | 漫画文件路径 |
| `BkMangaData` | String | Mokuro 漫画 JSON 数据 |

关系: `language` → Language, `texts` → Text[], `book_tags` → BookTag[]

**Text** (`texts` 表) — 图书的页面

| 列 | 类型 | 描述 |
|----|------|------|
| `TxID` | Integer PK | 主键 |
| `TxBkID` | Integer FK → `books.BkID` | 所属图书 |
| `TxOrder` | Integer | 页面顺序 |
| `TxText` | String | 页面文本内容 |
| `TxWordCount` | Integer | 页面词数 |
| `TxStartDate` | DateTime | 开始阅读时间 |
| `TxReadDate` | DateTime | 完成阅读时间 |

关系: `book` → Book, `bookmarks` → TextBookmark[], `sentences` → Sentence[]

**Sentence** (`sentences` 表) — 解析后的句子

| 列 | 类型 | 描述 |
|----|------|------|
| `SeID` | Integer PK | 主键 |
| `SeTxID` | Integer FK → `texts.TxID` | 所属文本 |
| `SeOrder` | Integer | 句子顺序 |
| `SeText` | Text | 句子内容（零宽空格分隔 tokens） |
| `SeTextLC` | Text | 小写化内容（`*` 表示与 parser 小写化一致） |

**BookStats** (`bookstats` 表) — 图书统计（视图/物化表）

| 列 | 类型 | 描述 |
|----|------|------|
| `BkID` | Integer PK | 图书 ID |
| `distinctterms` | Integer | 不同术语数 |
| `distinctunknowns` | Integer | 未知术语数 |
| `unknownpercent` | Integer | 未知词百分比 |
| `new_word_percent` | Integer | 新词百分比 |
| `status_distribution` | String | 状态分布 JSON |
| `manga_word_count` | Integer | 漫画词数 |

**WordsRead** (`wordsread` 表) — 阅读事件记录

**TextBookmark** (`textbookmarks` 表) — 页面书签

**BookTag** (`tags2` 表) — 图书标签

#### [lute/models/term.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/models/term.py)

**Term** (`words` 表) — 术语/单词实体

| 列 | 类型 | 描述 |
|----|------|------|
| `WoID` | SmallInteger PK | 主键 |
| `WoLgID` | Integer FK → `languages.LgID` | 所属语言 |
| `WoText` | String(250) | 文本（零宽空格分隔多词术语） |
| `WoTextLC` | String(250) | 小写文本 |
| `WoStatus` | Integer | 掌握状态 (0=未知, 1-5=学习中, 98=忽略, 99=熟知) |
| `WoTranslation` | String(500) | 翻译 |
| `WoRomanization` | String(100) | 读音 |
| `WoTokenCount` | Integer | 构成词数 |
| `WoSyncStatus` | Boolean | 是否与父词同步状态 |

关系: `language` → Language, `term_tags` ↔ TermTag (多对多), `parents` ↔ Term (自引用多对多), `images` → TermImage[], `term_flash_message` → TermFlashMessage

**TermTag** (`tags` 表) — 术语标签

**TermImage** (`wordimages` 表) — 术语图片

**TermFlashMessage** (`wordflashmessages` 表) — 术语闪卡消息

**Status** (`statuses` 表) — 状态定义

**关键常量：**
- `Status.UNKNOWN = 0` — 未知词
- `Status.WELLKNOWN = 99` — 熟知词
- `Status.IGNORED = 98` — 忽略词
- `Status.ALLOWED = [0, 1, 2, 3, 4, 5, 98, 99]` — 有效状态

**重要方法：**

| 方法 | 描述 |
|------|------|
| `Term.text.setter` | 设置文本时自动分词、小写化、计算 token 数；已存在的 term 不允许修改文本内容 |
| `Term.create_term_no_parsing()` | 不重新分词创建 Term（用于已解析页面场景） |
| `_parse_string_add_zws()` | 用零宽空格分隔已分词文本 |
| `add_parent()` / `remove_all_parents()` | 管理父子关系（用于词形变化，如动词原形 → 变形） |
| `get_flash_message()` / `pop_flash_message()` | 闪卡消息的获取与消费 |
| `set_current_image()` / `get_current_image()` | 管理术语图片 |

#### [lute/models/language.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/models/language.py)

**Language** (`languages` 表) — 语言实体

| 列 | 类型 | 描述 |
|----|------|------|
| `LgID` | SmallInteger PK | 主键 |
| `LgName` | String(40) | 语言名称 |
| `LgCharacterSubstitutions` | String(500) | 字符替换规则 |
| `LgRegexpSplitSentences` | String(500) | 分句正则 |
| `LgExceptionsSplitSentences` | String(500) | 分句例外 |
| `LgRegexpWordCharacters` | String(500) | 单词字符集 |
| `LgRightToLeft` | Boolean | 是否从右到左 |
| `LgShowRomanization` | Boolean | 是否显示读音 |
| `LgParserType` | String(20) | 解析器类型 |
| `LgIsActive` | Boolean | 是否激活 |
| `LgKiwiTokenizerMode` | String(20) | 韩语 Kiwi 分词模式 |
| `LgKiwiStemming` | Boolean | 韩语词干提取 |
| `LgKiwiFilterParticles` | Boolean | 韩语助词过滤 |
| `LgKiwiJoinCompoundNouns` | Boolean | 韩语复合名词合并 |

**LanguageDictionary** (`languagedicts` 表) — 语言词典配置

| 方法 | 描述 |
|------|------|
| `parser` | 属性，获取对应解析器实例 |
| `is_supported` | 检查解析器是否可用 |
| `get_parsed_tokens()` | 对文本进行分词 |
| `get_lowercase()` | 获取小写文本 |
| `active_dict_uris()` | 获取活跃词典 URI 列表 |
| `to_dict()` / `from_dict()` | 序列化/反序列化（用于预定义语言定义文件） |

#### [lute/models/setting.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/models/setting.py)

**SettingBase** (`settings` 表) — 设置基表（多态）
- `StKey` (String PK) — 键名
- `StKeyType` (String) — 类型标识 (`"user"` / `"system"`)
- `StValue` (String) — 值

**UserSetting** — 用户设置（多态 identity = "user"）
**SystemSetting** — 系统设置（多态 identity = "system"）
**BackupSettings** — 备份设置便捷包装类

#### [lute/models/repositories.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/models/repositories.py)

Repository 模式封装，提供数据库查询抽象层。

| 类 | 主要方法 | 描述 |
|----|---------|------|
| `SettingRepositoryBase` | `set_value()`, `get_value()`, `key_exists()`, `delete_key()` | 设置 CRUD 基类 |
| `UserSettingRepository` | 继承基类 + `get_backup_settings()`, `set_dynamic_value()` | 用户设置仓库 |
| `SystemSettingRepository` | 继承基类 | 系统设置仓库 |
| `LanguageRepository` | `find()`, `find_by_name()`, `all_dictionaries()` | 语言查询 |
| `TermTagRepository` | `find()`, `find_by_text()`, `find_or_create_by_text()` | 标签查询 |
| `TermRepository` | `find()`, `find_by_spec()`, `delete_empty_images()` | 术语查询 |
| `BookTagRepository` | `find_or_create_by_text()` | 图书标签 |
| `BookRepository` | `find()`, `find_by_title()` | 图书查询 |

---

### 4.3 阅读模块

#### [lute/read/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/read/routes.py)

阅读模块是 Lute 最核心的用户交互界面，负责展示分词后的文本、处理用户点击、编辑术语、管理阅读进度等。

| 路由 | 方法 | 处理函数 | 描述 |
|------|------|---------|------|
| `/read/<int:bookid>` | GET | `read()` | 打开图书到当前阅读位置 |
| `/read/<int:bookid>/page/<int:pagenum>` | GET | `read_page()` | 打开指定页面 |
| `/read/<int:bookid>/peek/<int:pagenum>` | GET | `peek_page()` | 预览页面（不更新阅读进度） |
| `/read/page_done` | POST | `page_done()` | 标记页面完成，记录阅读事件 |
| `/read/delete_page/<int:bookid>/<int:pagenum>` | GET | `delete_page()` | 删除页面 |
| `/read/new_page/<int:bookid>/<position>/<int:pagenum>` | GET/POST | `new_page()` | 新建页面 |
| `/read/save_player_data` | POST | `save_player_data()` | 保存音频播放器位置和书签 |
| `/read/save_youtube_player_data` | POST | `save_youtube_player_data()` | 保存 YouTube 视频位置 |
| `/read/bilibili/stream/mpd/<bvid>` | GET | `bilibili_mpd()` | 获取 Bilibili 视频的 DASH 清单 (MPD)，BaseURL 指向本地代理 |
| `/read/bilibili/stream/proxy/<bvid>/<stream_type>` | GET | `bilibili_proxy()` | 代理转发 Bilibili CDN 的 Range 字节流请求 |
| `/read/youtube_subtitle_words/<int:bookid>` | GET | `youtube_subtitle_words()` | 懒加载字幕分词 HTML（缓存） |
| `/read/start_reading/<int:bookid>/<int:pagenum>` | GET | `start_reading()` | AJAX 开始阅读，返回页面内容 |
| `/read/update_start_date/<int:bookid>/<int:pagenum>` | GET/POST | `update_start_date()` | 通过 sendBeacon 更新开始时间 |
| `/read/refresh_page/<int:bookid>/<int:pagenum>` | GET | `refresh_page()` | 刷新页面内容 |
| `/read/empty` | GET | `empty()` | 空白页 |
| `/read/termform/<int:langid>/<text>` | GET/POST | `term_form()` | 创建多词术语的表单 |
| `/read/edit_term/<int:term_id>` | GET/POST | `edit_term_form()` | 编辑术语 |
| `/read/term_bulk_edit_form` | GET | `term_bulk_edit_form()` | 批量编辑术语表单 |
| `/read/termpopup/<int:termid>` | GET | `term_popup()` | 获取术语弹出窗口 HTML |
| `/read/flashcopied` | GET | `flashcopied()` | 闪卡复制成功页面 |
| `/read/editpage/<int:bookid>/<int:pagenum>` | GET/POST | `edit_page()` | 编辑页面文本内容 |

**关键机制：**
- `_yt_subtitle_words_cache`: 模块级缓存，缓存 YouTube 字幕的分词结果，避免每次页面加载重复分词
- `_render_book_page()`: 核心渲染函数，根据图书类型（普通/YouTube/Bilibili/MP3/漫画）选择不同模板

#### [lute/read/service.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/read/service.py)

| 类/方法 | 描述 |
|---------|------|
| **Service** | 阅读服务 |
| `start_reading()` | 开始阅读：设置文本开始时间，获取段落，保存新状态-0 术语 |
| `get_paragraphs()` | 获取段落（不跟踪阅读进度） |
| `mark_page_read()` | 标记页面已读，记录 WordsRead，可选将所有未知词标记为熟知 |
| `update_start_date()` | 轻量更新开始时间 |
| `set_unknowns_to_known()` | 将页面中所有未知词设为熟知 |
| `bulk_status_update()` | 批量更新术语状态 |
| `manga_page_context()` | 构建漫画页面渲染上下文（图片 URL + 文本块 + 分词结果） |
| `get_popup_data()` | 构建术语弹出窗口数据（翻译、读音、图片、父词、组件词） |
| **TermPopup** | 弹出窗口数据对象 |
| `show` | 判断是否有内容可显示 |
| `term_and_parents_text()` | 术语文本 + 父词文本 |

#### [lute/read/render/service.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/read/render/service.py)

**RenderService** — 文本渲染引擎

| 方法 | 描述 |
|------|------|
| `get_textitems()` | 获取文本的 TextItem 数组（分词 → 查找术语 → 计算 TextItem） |
| `get_paragraphs()` | 获取分段分句的 TextItem 二维数组 |
| `find_all_Terms_in_string()` | 在字符串中查找所有匹配的术语 |
| `get_multiword_indexer()` | 构建多词索引器 (ahocorapy) |
| `_find_all_terms_in_tokens()` | 在已分词的 tokens 中查找所有匹配术语 |

#### [lute/read/render/text_item.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/read/render/text_item.py)

**TextItem** — 渲染的最小单元

| 属性 | 描述 |
|------|------|
| `text` | 原始文本 |
| `text_lc` | 小写文本 |
| `is_word` | 是否为单词 |
| `token_count` | 原始 token 数 |
| `display_count` | 显示 token 数（处理重叠） |
| `sentence_number` | 句子编号 |
| `paragraph_number` | 段落编号 |
| `term` | 关联的 Term 对象 |
| `wo_status` | 术语状态 |
| `display_text` | 实际显示文本（处理重叠部分） |
| `html_display_text` | 渲染到浏览器的文本 |
| `span_id` | HTML span ID |
| `status_class` | CSS 状态类名 |
| `html_class_string` | 完整 CSS 类名 |

#### [lute/read/render/calculate_textitems.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/read/render/calculate_textitems.py)

**核心算法 — 文本项重叠计算**

这是阅读渲染最核心的算法。当文本中有多个术语（单词和多词）重叠时，需要决定哪些 TextItem 实际显示。

**算法步骤：**
1. 为所有 token 创建单词 TextItem
2. 为所有匹配的多词术语创建 TextItem
3. 按 (起始索引, -token数) 排序
4. 从后往前将 TextItem ID "写入"输出数组
5. 计算每个 ID 的出现次数作为 display_count
6. 只保留 display_count > 0 的 TextItem

**示例：**
```
Token:      A   B   C   D   E   F   G   H   I
术语:       A   BC  CDE     EFGHI  FG
输出:       [A] [BC] [-D E] [-F G H I]
```

#### [lute/read/render/multiword_indexer.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/read/render/multiword_indexer.py)

**MultiwordTermIndexer** — 使用 `ahocorapy` 库实现的高效多词术语匹配

| 方法 | 描述 |
|------|------|
| `add()` | 添加术语到关键词树 |
| `search_all()` | 在 token 序列中搜索所有匹配的多词术语 |

#### [lute/read/bilibili_stream.py](file:///Users/cxi/Documents/lutedev/lute-v3/lute/read/bilibili_stream.py)

**Bilibili DASH 流代理** — 绕过官方播放器域名白名单限制

Bilibili 官方 iframe 播放器在非白名单域名下无法初始化（只显示封面、不加载视频）。本模块绕过播放器，直接播放原始 DASH 流（fragmented MP4 视频 + 音频）：

| 函数 | 描述 |
|------|------|
| `stream_info(bvid, page)` | 调用 Bilibili 公开 `view`/`playurl` API 获取分段 MP4 URL 与编解码元数据（30 分钟缓存） |
| `build_mpd(info, video_proxy, audio_proxy)` | 生成 on-demand DASH 清单 (MPD)，BaseURL 指向本地代理端点 |
| `proxy_stream(base_url, range_header)` | 将 dash.js 的 Range 请求转发到 Bilibili CDN，附加 Referer/UA 头，规避 CORS 与防盗链 |
| `invalidate_stream(bvid)` | 图书重新导入时清除流缓存 |
| `_fetch_view()` / `_fetch_playurl()` | 调用 Bilibili 公开 API 获取视频信息与播放地址 |
| `_pick(streams)` | 从 DASH 流列表中选取最高带宽流 |

---

### 4.4 分词解析模块

#### [lute/parse/base.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/parse/base.py)

**ParsedToken** — 分词结果 Token

| 属性 | 描述 |
|------|------|
| `token` | 分词文本 |
| `is_word` | 是否为单词 |
| `is_end_of_sentence` | 是否句子结束 |
| `is_end_of_paragraph` | 是否段落结束 |
| `order` | 全局顺序号 |
| `sentence_number` | 所属句子编号 |

**AbstractParser** — 解析器抽象基类

| 方法 | 描述 |
|------|------|
| `get_parsed_tokens(text, language)` | **抽象方法**：对文本分词 |
| `get_reading(text)` | 获取读音（可选） |
| `get_lemma(text)` | 获取词典形/原形（可选） |
| `get_lowercase(text)` | 获取小写（可重写，如土耳其语） |
| `is_supported()` | 检查解析器是否可用 |
| `name()` | **抽象方法**：解析器名称 |
| `languages()` | 返回适用语言集合，或 None（通用） |

#### [lute/parse/registry.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/parse/registry.py)

**解析器注册表** — 管理所有可用解析器

| 方法 | 描述 |
|------|------|
| `init_parser_plugins()` | 加载插件解析器（通过 `entry_points` 发现） |
| `get_parser(parser_name)` | 根据名称获取解析器实例 |
| `is_supported(parser_name)` | 检查解析器是否可用 |
| `supported_parsers()` | 列出所有可用解析器 |
| `supported_parser_types()` | 列出所有可用解析器类型名 |

**内置解析器注册：**

| 键名 | 类 | 描述 | 适用语言 |
|------|----|------|---------|
| `spacedel` | `SpaceDelimitedParser` | 空格分隔通用解析器 | 英语、法语、西班牙语等 |
| `turkish` | `TurkishParser` | 土耳其语特殊处理 | 土耳其语 |
| `japanese` | `JapaneseParser` | 日语 MeCab 解析器 | 日语 |
| `japanese_sudachi` | `JapaneseSudachiParser` | 日语 Sudachi 解析器 | 日语 |
| `korean` | `KoreanParser` | 韩语 Kiwi 解析器 | 韩语 |
| `classicalchinese` | `ClassicalChineseParser` | 文言文逐字解析器 | 文言文 |

#### [lute/parse/space_delimited_parser.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/parse/space_delimited_parser.py)

**SpaceDelimitedParser** — 通用空格分隔语言解析器

| 方法 | 描述 |
|------|------|
| `get_parsed_tokens()` | 主入口：清理文本 → `_parse_to_tokens()` |
| `_parse_to_tokens()` | 执行字符替换，按段落分块，每块调用 `parse_para()` |
| `parse_para()` | 使用语言配置的 `word_characters` 正则提取单词，`split_sentences` 分割句子 |
| `preg_match_capture()` | 正则匹配捕获（带位置信息） |
| `get_default_word_characters()` | 获取默认 Unicode 单词字符集（覆盖所有语言字母类别） |
| `get_default_regexp_split_sentences()` | 获取默认分句正则（Unicode 句末标点） |

**TurkishParser** — 继承 SpaceDelimitedParser，重写 `get_lowercase()` 处理土耳其语特殊 i 大小写规则。

#### [lute/parse/mecab_parser.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/parse/mecab_parser.py)

**JapaneseParser** — 日语 MeCab 解析器（使用 `natto-py`）

| 方法 | 描述 |
|------|------|
| `is_supported()` | 检查 MeCab 是否可用，自动检测路径 |
| `get_parsed_tokens()` | 使用 MeCab 解析文本，区分单词/非单词/句末 |
| `get_reading()` | 获取读音（支持 IPADIC 和 Unidic，支持ひらがな/カタカナ/アルファベット输出） |
| `get_lemma()` | 获取词典形（辞書形），自动跳过助词/助动词后缀 |
| `_detect_dict_type()` | 自动检测词典类型 (IPADIC/Unidic) |
| `_is_jiritsugo()` | 判断是否为自立语（独立词） |
| `_normalize_unidic_lemma()` | 标准化 Unidic 词典形（如 `為る` → `する`） |
| `_clean_lemma_value()` | 清理 Unidic 词典形中的 POS 分类信息 |

**关键机制：**
- MeCab 实例缓存：避免重复初始化（~18ms/次）
- 支持 IPADIC 和 Unidic 两种词典格式
- 自动检测词典路径（多架构 Docker 支持）
- 通过 `japanese_dict` 和 `japanese_reading` 用户设置控制

#### [lute/parse/kiwi_parser.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/parse/kiwi_parser.py)

**KoreanParser** — 韩语 Kiwi 解析器（使用 `kiwipiepy`）

基于 Kiwi 分词器，支持分词模式、词干提取、助词过滤、复合名词合并等配置。

#### [lute/parse/sudachi_parser.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/parse/sudachi_parser.py)

**JapaneseSudachiParser** — 日语 Sudachi 解析器（使用 `sudachipy`）

Sudachi 的替代分词方案，可配置分词模式（A/B/C），支持读音和原形提取。

#### [lute/parse/character_parser.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/parse/character_parser.py)

**ClassicalChineseParser** — 文言文逐字解析器

每个汉字作为独立 token，适用于文言文等没有空格分隔的语言。

---

### 4.5 图书管理模块

#### [lute/book/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/book/routes.py)

| 路由 | 方法 | 描述 |
|------|------|------|
| `/book/datatables/active` | POST | 活跃图书列表数据源 |
| `/book/datatables/Archived` | POST | 归档图书列表数据源 |
| `/book/archived` | GET | 归档图书页面 |
| `/book/new` | GET/POST | 新建图书（支持 TXT/EPUB/PDF/SRT/VTT 上传） |
| `/book/edit/<int:bookid>` | GET/POST | 编辑图书 |
| `/book/import_webpage` | GET/POST | 从 URL 导入网页 |
| `/book/archive/<int:bookid>` | POST | 归档图书 |
| `/book/unarchive/<int:bookid>` | POST | 取消归档 |
| `/book/delete/<int:bookid>` | POST | 删除图书 |
| `/book/table_stats/<int:bookid>` | GET | 单本书统计 |
| `/book/table_stats` | POST | 批量统计更新 |

**内部分配函数：**
- `_import_youtube_video()`: 处理 YouTube 视频导入（含字幕解析）
- `_import_bilibili_video()`: 处理 Bilibili 视频导入（URL + SRT/VTT 字幕文件，标题经 view API 获取）
- `_import_mp3_audio()`: 处理 MP3 音频导入（含字幕解析）
- `_import_mokuro_manga()`: 处理 Mokuro 漫画导入（zip/cbz 解压 + .mokuro JSON 解析）

#### [lute/book/service.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/book/service.py)

**FileTextExtraction** — 文件文本提取

| 方法 | 描述 |
|------|------|
| `get_file_content()` | 根据扩展名选择解析器，返回文本内容 |
| `_get_textfile_content()` | 读取 UTF-8 文本文件 |
| `_get_epub_content()` | 使用 `openepub` 解析 EPUB |
| `_get_pdf_content()` | 使用 `pypdf` 解析 PDF |
| `_get_srt_content()` | 解析 SRT 字幕文件 |
| `_get_vtt_content()` | 解析 WebVTT 字幕文件 |

**Service** — 图书导入服务

| 方法 | 描述 |
|------|------|
| `import_book()` | 导入图书主入口：处理文本/音频/漫画文件，保存到数据库 |
| `book_data_from_url()` | 从 URL 提取网页内容（使用 BeautifulSoup） |
| `youtube_title()` | 使用 YouTube oEmbed API 获取视频标题 |
| `bilibili_title()` | 使用 Bilibili view API 获取视频真实标题（无需 API key） |
| `save_audio_file()` | 保存音频文件到用户音频目录 |
| `extract_manga()` | 解压 Mokuro 漫画 zip/cbz，解析 .mokuro JSON |

**核心函数：**
- `youtube_video_id()`: 从 URL 提取 YouTube 视频 ID
- `bilibili_video_id()`: 从 URL 提取 Bilibili 视频 ID（返回 `(bvid, aid)` 元组，兼容 BV 与 av 格式）
- `bilibili_page()`: 提取分P集数（`?p=N`，默认 1）
- `bilibili_embed_url()`: 构建 Bilibili 播放器嵌入 URL
- `parse_subtitle_file()` / `parse_subtitle_content()`: 解析 SRT/VTT 字幕文件
- `cues_to_srt_text()`: 将字幕 cues 转为 SRT 格式文本

#### [lute/book/model.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/book/model.py)

**Repository** — 图书业务对象仓库

| 方法 | 描述 |
|------|------|
| `new_book()` | 创建新 Book 业务对象 |
| `load()` | 从数据库加载 Book |
| `add()` | 添加 Book 到数据库 |
| `delete()` | 删除 Book |

#### [lute/book/stats.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/book/stats.py)

**Service** — 图书统计服务

| 方法 | 描述 |
|------|------|
| `refresh_stats()` | 刷新所有图书统计 |
| `mark_stale()` | 标记图书统计为过期 |
| `get_stats()` | 获取单本书统计 |
| `get_stats_batch()` | 批量获取统计 |

---

### 4.6 术语管理模块

#### [lute/term/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/term/routes.py)

| 路由 | 方法 | 描述 |
|------|------|------|
| `/term/index` | GET | 术语列表页 |
| `/term/datatables` | POST | 术语 DataTables 数据源 |
| `/term/new` | GET/POST | 新建术语 |
| `/term/edit/<int:termid>` | GET/POST | 编辑术语 |
| `/term/delete/<int:termid>` | POST | 删除术语 |
| `/term/search` | GET | 搜索术语 |
| `/term/apply_ajax_update` | POST | AJAX 更新术语（翻译、状态、标签等） |
| `/term/references/<int:termid>` | GET | 术语引用列表 |
| `/term/bulk_update` | POST | 批量更新术语状态 |

#### [lute/term/model.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/term/model.py)

**Term** (Business Object) — 术语业务对象

| 属性 | 描述 |
|------|------|
| `id` | 数据库 ID |
| `language_id` | 语言 ID |
| `text` | 文本 |
| `text_lc` | 小写文本 |
| `translation` | 翻译 |
| `romanization` | 读音 |
| `status` | 掌握状态 |
| `term_tags` | 标签列表 |
| `parents` | 父词列表 |
| `current_image` | 当前图片 |
| `flash_message` | 闪卡消息 |
| `sync_status` | 是否同步状态 |

**Repository** — 术语业务对象仓库

| 方法 | 描述 |
|------|------|
| `find(langid, text)` | 查找术语，返回 None 如果不存在 |
| `find_or_new(langid, text)` | 查找或创建新术语 |
| `find_matches(langid, text)` | 模糊匹配搜索 |
| `load(term_id)` | 按 ID 加载 |
| `add(term)` | 添加到数据库会话 |
| `delete(term)` | 标记删除 |
| `commit()` | 提交并清空身份映射 |

**关键机制 — 父子关系同步：**
- 当 `sync_status = True` 且只有一个父词时，子词和父词的状态自动同步
- 设置父词时，如果父词不存在或状态为 0，会自动继承子词的状态、翻译和图片

**ReferencesRepository** — 术语引用查询

| 方法 | 描述 |
|------|------|
| `find_references(term)` | 查找术语本身、子词、父词的所有引用 |
| `find_references_by_id(term_id)` | 按 ID 查找引用 |

引用查询通过 `sentences` 表的 `SeText` 字段进行 `LIKE` 匹配，支持高亮显示引用上下文中匹配的术语。

---

### 4.7 数据库模块

#### [lute/db/__init__.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/db/__init__.py)
```python
db = SQLAlchemy()
```
单例，供全局使用。

#### [lute/db/setup/main.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/db/setup/main.py)

**Setup** — 数据库初始化流程

| 步骤 | 描述 |
|------|------|
| 1 | 检查数据库文件是否存在 |
| 2 | 如果不存在，从 `baseline.sql` 创建基线 |
| 3 | 检查是否有待迁移 |
| 4 | 如果有迁移，先备份数据库 |
| 5 | 运行所有待迁移 SQL 脚本 |

**BackupManager** — 数据库备份管理器

| 方法 | 描述 |
|------|------|
| `do_backup()` | 创建 gzip 压缩备份，清理旧备份 |

#### [lute/db/setup/migrator.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/db/setup/migrator.py)

**SqliteMigrator** — SQLite 迁移引擎

| 方法 | 描述 |
|------|------|
| `do_migration()` | 运行所有迁移（先单向迁移，后可重复迁移） |
| `has_migrations()` | 检查是否有未应用的迁移 |
| `_get_pending()` | 获取未应用的迁移文件列表 |
| `_process_folder()` | 运行并记录单向迁移到 `_migrations` 表 |
| `_process_repeatable()` | 运行可重复迁移（始终运行） |

**迁移机制：**
- 迁移文件命名：`YYYYMMDD_HHMMSS_description.sql`
- 已执行迁移记录在 `_migrations` 表中
- 可重复迁移（如触发器）始终执行

#### [lute/db/management.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/db/management.py)

| 函数 | 描述 |
|------|------|
| `add_default_user_settings()` | 初始化默认用户设置（备份、TTS、Anki、主题等） |
| `delete_all_data()` | 危险操作：删除所有数据，重置用户设置 |
| `_revised_mecab_path()` | 自动修正 MeCab 库路径（多架构 Docker 支持） |

#### [lute/db/demo.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/db/demo.py)

**DemoService** — 演示数据管理

| 方法 | 描述 |
|------|------|
| `should_load_demo_data()` | 检查是否应加载演示数据 |
| `contains_demo_data()` | 检查是否包含演示数据 |
| `load_demo_data()` | 加载演示语言和故事 |
| `load_demo_languages()` | 加载 13 种预定义语言 |
| `load_demo_stories()` | 加载每种语言的故事 |
| `delete_demo_data()` | 删除演示数据 |
| `tutorial_book_id()` | 获取教程图书 ID |

---

### 4.8 语言管理模块

#### [lute/language/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/language/routes.py)

| 路由 | 方法 | 描述 |
|------|------|------|
| `/language/index` | GET | 语言列表页 |
| `/language/list_predefined` | GET | 预定义语言列表 |
| `/language/new` | GET/POST | 新建语言（支持从预定义导入） |
| `/language/edit/<int:langid>` | GET/POST | 编辑语言配置 |
| `/language/delete/<int:langid>` | POST | 删除语言 |
| `/language/activate/<int:langid>` | POST | 激活语言 |
| `/language/deactivate/<int:langid>` | POST | 冻结/停用语言 |

#### [lute/language/service.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/language/service.py)

| 方法 | 描述 |
|------|------|
| `get_language_def(name)` | 从预定义语言 YAML 文件加载语言定义 |
| `load_language(lang_def)` | 将语言定义导入数据库 |
| `get_available_language_names()` | 获取所有可用预定义语言名称 |

**预定义语言定义文件位于：** `lute/db/language_defs/`，每个语言一个子目录，包含 `definition.yaml` 和故事文本。

---

### 4.9 设置模块

#### [lute/settings/current.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/settings/current.py)

**全局设置缓存机制**

```python
current_settings = {}  # 全局键值对缓存
current_hotkeys = {}   # 快捷键映射
```

| 函数 | 描述 |
|------|------|
| `refresh_global_settings(session)` | 从数据库重新加载所有设置到缓存，转换布尔值 |

**布尔键自动转换列表：** `open_popup_in_new_tab`, `stop_audio_on_term_form_open`, `show_highlights`, `term_popup_promote_parent_translation`, `term_popup_show_components`, `use_ankiconnect`, `show_streak_on_home`, `tts_hover_pronunciation`, `tts_click_pronunciation`, `tts_show_control_panel`, `tts_show_sentence_buttons`

#### [lute/settings/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/settings/routes.py)

| 路由 | 方法 | 描述 |
|------|------|------|
| `/settings/index` | GET/POST | 设置页面 |
| `/settings/shortcuts` | GET/POST | 快捷键设置 |

---

### 4.10 TTS 语音合成与翻译

#### [lute/tts/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/tts/routes.py)

本模块提供 Edge-TTS 语音合成和 Google/MyMemory 自动翻译服务。

| 路由 | 方法 | 描述 |
|------|------|------|
| `/tts/<lang>/<path:text>` | GET | 生成并返回指定语言和文本的 MP3 音频 |
| `/api/translate/<sl>/<tl>/<path:text>` | GET | 返回翻译结果 JSON |

**关键数据：**

`VOICE_MAP` — 30+ 语言的 Edge-TTS 语音映射

`LANG_NAME_TO_CODE` — 40+ 语言的名称到 ISO 639-1 代码映射

**核心函数：**

| 函数 | 描述 |
|------|------|
| `tts_speak()` | 语音合成：使用 MD5 缓存 → 使用 Edge-TTS 生成 → 返回 MP3 |
| `_generate_audio()` | 异步调用 Edge-TTS `Communicate.save()` |
| `translate()` | 翻译：先查内存缓存 → Google API → MyMemory 回退 |
| `_translate_via_google()` | 调用 Google Translate API |
| `_translate_via_mymemory()` | 调用 MyMemory API（回退方案） |
| `get_lang_code()` | 根据语言名称获取 ISO 语言代码 |

**缓存机制：**
- 语音缓存：`DATAPATH/tts_cache/` 目录，MD5 文件名
- 翻译缓存：内存 dict `trans_cache`

---

### 4.11 Anki 导出模块

#### [lute/ankiexport/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/ankiexport/routes.py)

| 路由 | 方法 | 描述 |
|------|------|------|
| `/ankiexport/index` | GET/POST | 导出规格列表 |
| `/ankiexport/spec/new` | GET/POST | 新建导出规格 |
| `/ankiexport/spec/edit/<int:spec_id>` | GET/POST | 编辑导出规格 |
| `/ankiexport/spec/delete/<int:spec_id>` | GET/POST | 删除导出规格 |
| `/ankiexport/get_card_post_data` | POST | 获取 AnkiConnect 请求数据 |
| `/ankiexport/validate_export_specs` | POST | 验证导出规格 |

#### [lute/ankiexport/service.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/ankiexport/service.py)

| 方法 | 描述 |
|------|------|
| `get_ankiconnect_post_data()` | 构建 AnkiConnect 请求 JSON |
| `validate_specs()` | 验证所有导出规格 |
| `evaluate_criteria()` | 根据筛选条件过滤术语 |
| `get_values_and_media_mapping()` | 将字段映射应用到术语 |

#### [lute/ankiexport/criteria.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/ankiexport/criteria.py)

筛选器解析器，支持：标签筛选、语言筛选、状态筛选、父标签筛选、组合条件。

---

### 4.12 备份恢复模块

#### [lute/backup/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/backup/routes.py)

| 路由 | 方法 | 描述 |
|------|------|------|
| `/backup/index` | GET | 备份列表页 |
| `/backup/backup` | GET | 备份表单页 |
| `/backup/do_backup` | POST | AJAX 执行备份 |
| `/backup/skip_this_backup` | GET | 跳过本次备份 |
| `/backup/restore` | POST | 上传文件恢复备份 |
| `/backup/restore_file/<filename>` | POST | 从备份目录恢复 |
| `/backup/download/<filename>` | GET | 下载备份文件 |

#### [lute/backup/service.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/backup/service.py)

| 方法 | 描述 |
|------|------|
| `create_backup()` | 创建时间戳命名的 gzip 备份 |
| `restore_backup()` | 从备份文件恢复数据库 |
| `list_backups()` | 列出所有备份文件 |
| `should_run_auto_backup()` | 判断是否应运行自动备份 |
| `backup_warning()` | 获取备份警告信息 |

---

### 4.13 前端模块

#### JavaScript 文件概览

| 文件 | 描述 |
|------|------|
| [lute.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/lute.js) | 主前端交互逻辑：页面渲染、AJAX 请求、弹窗管理、术语编辑、TTS/音视频播放器初始化 |
| [tts.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/tts.js) | TTS 语音播放控制、缓存管理、播放器集成 |
| [player.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/player.js) | 音频播放器（播放、暂停、跳转、书签、速率控制） |
| [lute-anki.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/lute-anki.js) | Anki 导出：术语选择、导出配置、AnkiConnect 请求 |
| [lute-popups.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/lute-popups.js) | 弹出窗口交互（模态框打开/关闭/动态加载） |
| [youtube-player.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/youtube-player.js) | YouTube 播放器集成（IFrame API） |
| [bilibili-player.js](file:///Users/cxi/Documents/lutedev/lute-v3/lute/static/js/bilibili-player.js) | Bilibili 播放器集成（dash.js 直连 DASH 流，复用与 YouTube 相同的 UI/滚动字幕/循环控制） |
| [dict-tabs.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/dict-tabs.js) | 词典选项卡管理 |
| [text-options.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/text-options.js) | 文本显示选项（字体大小、行间距、列数） |
| [resize.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/resize.js) | 组件大小自适应调整 |
| [lute-hotkey-utils.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/lute-hotkey-utils.js) | 键盘快捷键支持 |
| [lute-tagify-utils.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/lute-tagify-utils.js) | Tagify 标签输入组件集成 |

#### 主题 CSS

内置主题存放在 [lute/themes/css/](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/themes/css/)：
`Default.css`, `Default-Dark.css`, `Dark_slate.css`, `Night.css`, `Dusk.css`, `Apple_Books.css`, `Black_and_White.css`, `LWT.css`, `LingQ.css`

---

### 4.14 其他辅助模块

#### [lute/stats/](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/stats/)

阅读统计模块，使用 Chart.js 绘制图表。

| 路由 | 描述 |
|------|------|
| `/stats/` | 统计主页面 |
| `/stats/data` | 阅读数据 JSON（用于图表） |
| `/stats/term_data` | 术语统计数据 JSON |

**Service.get_reading_streak()** — 计算连续阅读天数

#### [lute/bing/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/bing/routes.py)

Bing 图片搜索，支持保存图片到用户目录。

| 路由 | 描述 |
|------|------|
| `/bing/search_page/<langid>/<text>/<searchstring>` | 图片搜索页面 |
| `/bing/search/<langid>/<text>/<searchstring>` | 执行搜索 |
| `/bing/save` | 保存图片 |

#### [lute/userimage/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/userimage/routes.py)

| 路由 | 描述 |
|------|------|
| `/userimages/<int:lgid>/<path:f>` | GET 服务用户图片 |

#### [lute/useraudio/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/useraudio/routes.py)

| 路由 | 描述 |
|------|------|
| `/useraudio/stream/<int:bookid>` | GET 流式播放用户音频 |

#### [lute/cli/commands.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/cli/commands.py)

Flask CLI 命令，通过 `flask cli <command>` 调用。

| 命令 | 描述 |
|------|------|
| `cli hello` | 测试命令 |
| `cli language_export` | 导出语言术语 |
| `cli book_term_export` | 导出图书术语 |
| `cli import_books_from_csv` | 从 CSV 批量导入图书 |

#### [lute/utils/data_tables.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/utils/data_tables.py)

DataTables SQL 查询构建器，支持服务器端分页、排序、搜索。

#### [lute/themes/](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/themes/)

主题管理模块，支持主题切换、自定义 CSS、用户上传主题。

---

## 5. 数据模型

### ER 图核心关系

```
Language (languages)
  ├── Book (books)          ──────── has_many Text (texts)
  │     └── Text (texts)    ──────── has_many Sentence (sentences)
  │     └── Text            ──────── has_many TextBookmark (textbookmarks)
  │     └── BookStats (bookstats)   ── 物化视图
  ├── Term (words)          ──────── has_many TermImage (wordimages)
  │     └── Term            ──────── has_many self parents (wordparents)
  │     └── Term            ──────── has_many TermFlashMessage (wordflashmessages)
  │     └── TermTag (tags)  ──────── many-to-many (wordtags)
  ├── LanguageDictionary (languagedicts)
  └── Status (statuses)     ──────── 状态定义

Settings (settings) ──── polymorphic: UserSetting / SystemSetting

WordsRead (wordsread) ── 阅读事件记录
```

### 关键关联表

| 表名 | 用途 | 关联 |
|------|------|------|
| `wordparents` | 术语父子关系（词形变化） | words.WoID ← → words.WoID |
| `wordtags` | 术语-标签多对多 | words.WoID ↔ tags.TgID |
| `booktags` | 图书-标签多对多 | books.BkID ↔ tags2.T2ID |

### 术语状态机

```
0 (Unknown) ─→ 1 ─→ 2 ─→ 3 ─→ 4 ─→ 5 ─→ 99 (Well Known)
                                            98 (Ignored)
```

状态 0 是自动创建的未知词，用户通过阅读交互逐步提升状态。

---

## 6. 路由总览

### 基础路由 (app_factory.py)

| 路径 | 方法 | 描述 |
|------|------|------|
| `/` | GET | 首页 |
| `/refresh_all_stats` | GET/POST | 刷新所有统计 |
| `/wipe_database` | GET | 清除演示数据 |
| `/remove_demo_flag` | GET | 退出演示模式 |
| `/version` | GET | 版本信息页 |
| `/info` | GET | JSON 信息 API |
| `/static/js/never_cache/<path:filename>` | GET | 带缓存的 JS 文件 |
| `/sw.js` | GET | PWA Service Worker |
| `/manifest.webmanifest` | GET | PWA Manifest |

### 蓝图路由汇总

| 蓝图 | 前缀 | 路由数 | 主要功能 |
|------|------|--------|---------|
| `read` | `/read` | 21 | 阅读页面、术语编辑、播放器控制、Bilibili 流代理 |
| `book` | `/book` | 11 | 图书 CRUD、导入（含 Bilibili）、统计 |
| `term` | `/term` | 10 | 术语 CRUD、搜索、批量操作 |
| `language` | `/language` | 7 | 语言 CRUD、激活/冻结 |
| `settings` | `/settings` | 2 | 用户设置、快捷键 |
| `backup` | `/backup` | 7 | 备份、恢复、下载 |
| `ankiexport` | `/ankiexport` | 6 | Anki 导出规格管理 |
| `tts` | (无前缀) | 2 | TTS 语音合成、翻译 |
| `stats` | `/stats` | 3 | 阅读统计、图表数据 |
| `themes` | `/theme` | 7 | 主题切换、自定义、上传 |
| `bing` | `/bing` | 4 | 图片搜索、保存 |
| `userimage` | `/userimages` | 1 | 图片服务 |
| `useraudio` | `/useraudio` | 1 | 音频流媒体 |
| `bookmarks` | `/bookmarks` | 1 | 书签列表 |
| `termtag` | `/termtag` | 4 | 标签 CRUD |
| `termimport` | `/termimport` | 1 | 术语导入 |
| `term_parent_map` | `/term_parent_map` | 1 | 父子关系视图 |
| `cli` | (Flask CLI) | 4 | 命令行工具 |
| `dev_api` | `/dev_api` | (仅测试) | 测试 API |

---

## 7. 依赖关系

### 核心依赖 (pyproject.toml)

| 包 | 用途 |
|----|------|
| `Flask-SQLAlchemy>=3.1.1` | ORM 框架 |
| `Flask-WTF>=1.2.1` | 表单处理 |
| `natto-py>=1.0.1` | MeCab 日语分词 Python 绑定 |
| `jaconv>=0.3.4` | 日语假名转换 |
| `platformdirs>=3.10.0` | 跨平台数据目录 |
| `requests>=2.31.0` | HTTP 请求（翻译 API、网页导入） |
| `beautifulsoup4>=4.12.2` | HTML 解析（网页导入） |
| `PyYAML>=6.0.1` | YAML 配置解析 |
| `waitress>=2.1.2` | 生产 WSGI 服务器 |
| `openepub>=0.0.9` | EPUB 解析 |
| `pypdf>=3.17.4` | PDF 解析 |
| `subtitle-parser>=1.3.0` | SRT 字幕解析 |
| `ahocorapy>=1.6.2` | 多词术语匹配算法 |
| `edge-tts>=6.1.0` | Microsoft Edge TTS 语音合成 |

### 可选依赖

| 包 | 用途 |
|----|------|
| `sudachipy + sudachidict-core` | 日语 Sudachi 分词 |
| `kiwipiepy>=0.15.0` | 韩语 Kiwi 分词 |

### 开发依赖

| 包 | 用途 |
|----|------|
| `playwright` | 浏览器自动化测试 |
| `pytest-bdd` | BDD 测试框架 |
| `pylint` | 代码检查 |
| `black` | 代码格式化 |
| `coverage` | 测试覆盖率 |
| `pre-commit` | Git 钩子管理 |

---

## 8. 项目运行方式

### 环境要求

- Python >= 3.8
- SQLite 3 (系统自带)
- （可选）MeCab 日语分词库
- （可选）Kiwi 韩语分词库

### 快速开始（开发模式）

```bash
# 1. 克隆项目
git clone https://github.com/luteorg/lute-v3.git
cd lute-v3

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或 venv\Scripts\activate  # Windows

# 3. 安装依赖
pip install -e .

# 4. 配置
# 编辑 lute/config/config.yml 或使用默认配置
# 默认使用 lute/config/config.yml 文件

# 5. 启动开发服务器
python devstart.py --port 5001
# 或使用 Invoke
inv start --port 5001
```

### 生产部署

```bash
# 使用生产入口
python -m lute.main --port 5001

# 或指定配置文件
python -m lute.main --config /path/to/config.yml --port 5001

# 仅本地访问
python -m lute.main --local --port 5001
```

### Docker 部署

```bash
# 使用 Docker Compose
cd docker
docker-compose up -d

# 或手动构建
docker build -t lute-v3 .
docker run -p 5001:5001 lute-v3
```

### 配置文件说明

```yaml
# lute/config/config.yml
ENV: prod                    # 环境: prod / dev
DBNAME: lute.db              # 数据库文件名
DATAPATH: /path/to/data      # 用户数据目录（可选，默认使用平台目录）
BACKUP_PATH: /path/to/backups # 备份目录（可选）
```

### 测试

```bash
# 先确保 config.yml 中的数据库名以 test_ 开头

# 运行单元测试
inv test

# 运行验收测试（需要 Playwright）
inv accept

# 运行全部测试
inv fulltest

# 运行测试覆盖率
inv coverage --html
```

### 命令行工具

```bash
# 导出语言术语
flask cli language_export

# 导出图书术语
flask cli book_term_export

# 从 CSV 批量导入图书
flask cli import_books_from_csv
```

### 数据库迁移

```bash
# 创建新的迁移脚本
inv db.newscript --suffix "add_new_feature"

# 导出基线
inv db.export.baseline

# 重置数据库（仅测试环境）
inv db.reset
```

---

## 附录：关键设计决策

### 1. 零宽空格 (ZWS) 作为 Token 分隔符

Lute 使用 Unicode 零宽空格 (`\u200B`) 作为 Token 之间的分隔符。这允许在字符串中保留 Token 边界信息，同时不影响显示。多词术语的文本使用 ZWS 连接各个 Token。

### 2. 身份映射 (Identity Map)

`term.model.Repository` 维护一个身份映射字典，避免在同一个请求中多次加载同一个术语。这对于阅读页面渲染时频繁的术语查找非常重要。

### 3. 阅读渲染的"从后往前"算法

在计算重叠 TextItem 时，使用"从后往前写入"的策略：先处理较长的多词术语，再处理较短的，确保最长的文本块优先显示。

### 4. 父子词同步

术语可以设置父词（如动词原形），当 `sync_status=True` 时，子词和父词的状态自动同步。这允许用户只需标记一个变形词，其原形词也会获得相同状态。

### 5. 设置缓存

所有用户设置缓存在全局 dict `current_settings` 中，避免每次查询都访问数据库。设置变更后调用 `refresh_global_settings()` 刷新缓存。