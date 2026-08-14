# Lute v3 Code Wiki

> Version: 3.10.3.8 | Based on [LuteOrg/lute-v3](https://github.com/luteorg/lute-v3) with enhanced features: TTS, auto-translate, Bilibili support, and more.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Technical Architecture](#2-technical-architecture)
3. [Project Directory Structure](#3-project-directory-structure)
4. [Core Modules](#4-core-modules)
   - [4.1 Application Entry & Factory](#41-application-entry--factory)
   - [4.2 Data Model Layer](#42-data-model-layer)
   - [4.3 Reading Module](#43-reading-module)
   - [4.4 Parsing Module](#44-parsing-module)
   - [4.5 Book Management Module](#45-book-management-module)
   - [4.6 Term Management Module](#46-term-management-module)
   - [4.7 Database Module](#47-database-module)
   - [4.8 Language Management Module](#48-language-management-module)
   - [4.9 Settings Module](#49-settings-module)
   - [4.10 TTS & Translation](#410-tts--translation)
   - [4.11 Anki Export Module](#411-anki-export-module)
   - [4.12 Backup & Restore Module](#412-backup--restore-module)
   - [4.13 Frontend Module](#413-frontend-module)
   - [4.14 Other Auxiliary Modules](#414-other-auxiliary-modules)
5. [Data Models](#5-data-models)
6. [Route Overview](#6-route-overview)
7. [Dependencies](#7-dependencies)
8. [Running the Project](#8-running-the-project)

---

## 1. Project Overview

**Lute** (Learning Using Texts) is a **self-hosted language learning reading platform** that helps users learn foreign languages by reading authentic texts. Its core philosophy:

- Import foreign language texts (books, web pages, YouTube/Bilibili videos, MP3 audio, manga, etc.)
- Tokenize text and identify each word/term
- Users click words to view translations, readings, images, and mark mastery status
- Supports 60+ language parsers
- Built-in TTS speech synthesis, auto-translation, Anki card export, reading statistics, etc.

This branch extends the original Lute v3 with: TTS synthesis (Edge-TTS), auto-translation (Google/MyMemory), backup/restore UI, language freeze/activate, draggable column resize, Bilibili video import with DASH streaming playback, and more.

---

## 2. Technical Architecture

| Layer | Technology Stack |
|-------|-----------------|
| **Backend Framework** | Flask 3.x (Python 3.8+) |
| **ORM** | Flask-SQLAlchemy 3.x |
| **Database** | SQLite |
| **Template Engine** | Jinja2 |
| **Frontend** | jQuery + DataTables + jQuery UI + Tagify |
| **WSGI Server** | Waitress (production) / Werkzeug (development) |
| **Tokenization Engines** | MeCab (Japanese), Sudachi (Japanese), Kiwi (Korean), Space-delimited (most languages) |
| **Speech Synthesis** | Edge-TTS (Microsoft) |
| **Translation APIs** | Google Translate + MyMemory (fallback) |
| **Build Tool** | Flit (pyproject.toml) |
| **Testing** | pytest + behave (BDD) + Playwright |

### Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                     User Browser (Web UI)                     │
│       jQuery + DataTables + Tagify + Custom JS Modules       │
└─────────────────────────┬────────────────────────────────────┘
                          │ HTTP / AJAX
┌─────────────────────────▼────────────────────────────────────┐
│                    Flask Application (app_factory)            │
│   ┌─────────┐ ┌──────────┐ ┌────────┐ ┌──────────────────┐  │
│   │Blueprint│ │Request/  │ │Template│ │  Error           │  │
│   │Registry │ │Response  │ │Injection│ │  Handling        │  │
│   └─────────┘ └──────────┘ └────────┘ └──────────────────┘  │
└─────────────────────────┬────────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────────┐
│                  Core Business Modules (Blueprints)           │
│  ┌───────┐ ┌────────┐ ┌──────┐ ┌──────┐ ┌───────┐ ┌──────┐  │
│  │ Read  │ │ Book   │ │ Term │ │ Lang │ │ TTS   │ │Backup│  │
│  │ /read │ │ /book  │ │/term │ │ /lang│ │ /tts  │ │/back │  │
│  └───┬───┘ └───┬────┘ └──┬───┘ └──┬───┘ └───┬───┘ └───┬───┘  │
│      │         │         │        │         │         │       │
│  ┌───▼─────────▼─────────▼────────▼─────────▼─────────▼───┐  │
│  │                      Service Layer                       │  │
│  └──────────────────────────┬───────────────────────────────┘  │
└─────────────────────────────┼─────────────────────────────────┘
                              │
┌─────────────────────────────▼─────────────────────────────────┐
│           Data Model Layer (SQLAlchemy ORM) + Repository       │
│  Book / Text / Term / Language / Setting / Sentence / ...     │
└─────────────────────────────┬─────────────────────────────────┘
                              │
┌─────────────────────────────▼─────────────────────────────────┐
│                    SQLite Database File                         │
│   (lute.db / user-defined path)                                │
└───────────────────────────────────────────────────────────────┘
```

---

## 3. Project Directory Structure

```
lute-v3/
├── lute/                          # Main application package
│   ├── __init__.py                # Version info (__version__)
│   ├── main.py                    # Production entry (waitress serve)
│   ├── app_factory.py             # Flask application factory (core)
│   ├── config/                    # Configuration module
│   │   ├── app_config.py          # AppConfig class, YAML config loading
│   │   ├── config.yml.example     # Example config
│   │   ├── config.yml.prod        # Production config template
│   │   └── config.yml.docker      # Docker config
│   ├── models/                    # SQLAlchemy data models
│   │   ├── book.py                # Book, Text, Sentence, BookStats, etc.
│   │   ├── term.py                # Term, TermTag, TermImage, Status
│   │   ├── language.py            # Language, LanguageDictionary
│   │   ├── setting.py             # UserSetting, SystemSetting, BackupSettings
│   │   ├── srsexport.py           # SRSExportSpec
│   │   └── repositories.py        # Repository pattern wrappers
│   ├── db/                        # Database management
│   │   ├── __init__.py            # SQLAlchemy singleton
│   │   ├── setup/main.py          # DB init + migration management
│   │   ├── setup/migrator.py      # SqliteMigrator (SQL migration engine)
│   │   ├── demo.py                # Demo data loading
│   │   ├── management.py          # Data management (delete, defaults)
│   │   ├── data_cleanup.py        # Data cleanup
│   │   └── schema/                # SQL migration scripts
│   │       ├── baseline.sql       # Database baseline
│   │       ├── migrations/        # One-way migrations (50+ SQL files)
│   │       └── migrations_repeatable/  # Repeatable migrations
│   ├── read/                      # Reading module (core)
│   │   ├── routes.py              # Reading routes
│   │   ├── service.py             # Reading service (TermPopup, Service)
│   │   ├── bilibili_stream.py     # Bilibili DASH stream proxy (view/playurl → MPD → proxy)
│   │   ├── forms.py               # Reading forms
│   │   └── render/                # Text rendering engine
│   │       ├── service.py         # RenderService
│   │       ├── text_item.py       # TextItem data structure
│   │       ├── calculate_textitems.py  # TextItem calculation algorithm
│   │       └── multiword_indexer.py    # Multi-word indexer (ahocorapy)
│   ├── parse/                     # Parsing module
│   │   ├── base.py                # ParsedToken, AbstractParser base class
│   │   ├── registry.py            # Parser registry + plugin mechanism
│   │   ├── space_delimited_parser.py  # Space-delimited parser
│   │   ├── mecab_parser.py        # Japanese MeCab parser
│   │   ├── sudachi_parser.py      # Japanese Sudachi parser
│   │   ├── kiwi_parser.py         # Korean Kiwi parser
│   │   └── character_parser.py    # Classical Chinese character-by-character parser
│   ├── book/                      # Book management module
│   │   ├── routes.py              # Book CRUD + import routes
│   │   ├── service.py             # Book import service (FileTextExtraction)
│   │   ├── model.py               # Book business object + Repository
│   │   ├── stats.py               # Book statistics Service
│   │   ├── forms.py               # Book forms
│   │   ├── datatables.py          # DataTables data source
│   │   └── japanese_srt.py        # Japanese subtitle re-splitting
│   ├── term/                      # Term management module
│   │   ├── routes.py              # Term CRUD routes
│   │   ├── model.py               # Term business object + Repository + ReferencesRepository
│   │   ├── service.py             # Term Service
│   │   ├── forms.py               # Term forms
│   │   └── datatables.py          # Term DataTables
│   ├── language/                  # Language management module
│   │   ├── routes.py              # Language CRUD routes
│   │   ├── service.py             # Language Service (predefined languages)
│   │   └── forms.py               # Language forms
│   ├── settings/                  # User settings module
│   │   ├── current.py             # Global settings cache (current_settings)
│   │   ├── routes.py              # Settings routes
│   │   ├── forms.py               # Settings forms
│   │   └── hotkey_data.py         # Default hotkey values
│   ├── tts/                       # TTS speech synthesis
│   │   └── routes.py              # /tts/ and /api/translate/ routes
│   ├── ankiexport/                # Anki export
│   │   ├── routes.py              # Export routes
│   │   ├── service.py             # Export Service
│   │   ├── criteria.py            # Filter criteria parser
│   │   ├── field_mapping.py       # Field mapping
│   │   ├── forms.py               # Export forms
│   │   └── exceptions.py          # Exception definitions
│   ├── backup/                    # Backup & restore
│   │   ├── routes.py              # Backup routes
│   │   └── service.py             # Backup Service
│   ├── themes/                    # Theme management
│   │   ├── routes.py              # Theme routes
│   │   ├── service.py             # Theme Service
│   │   └── css/                   # Built-in theme CSS
│   ├── stats/                     # Reading statistics
│   │   ├── routes.py              # Stats routes
│   │   └── service.py             # Stats Service
│   ├── bookmarks/                 # Bookmarks
│   │   ├── routes.py
│   │   └── datatables.py
│   ├── bing/                      # Bing image search
│   │   └── routes.py
│   ├── cli/                       # CLI tools
│   │   ├── commands.py            # Flask CLI commands
│   │   ├── import_books.py        # CSV batch import
│   │   └── language_term_export.py # Language/book term export
│   ├── term_parent_map/           # Term parent-child relationship
│   │   ├── routes.py
│   │   └── service.py
│   ├── termimport/                # Term batch import
│   │   ├── routes.py
│   │   └── service.py
│   ├── termtag/                   # Term tag management
│   │   ├── routes.py
│   │   ├── forms.py
│   │   └── datatables.py
│   ├── userimage/                 # User image serving
│   │   └── routes.py
│   ├── useraudio/                 # User audio serving
│   │   └── routes.py
│   ├── dev_api/                   # Development API (testing only)
│   │   └── routes.py
│   ├── utils/                     # Utility modules
│   │   ├── data_tables.py         # DataTables SQL query builder
│   │   ├── formutils.py           # Form utility functions
│   │   └── debug_helpers.py       # Debug helpers
│   ├── templates/                 # Jinja2 templates
│   │   ├── base.html              # Base layout
│   │   ├── index.html             # Home page
│   │   └── read/ book/ term/ ...  # Module-specific templates
│   └── static/                    # Static assets
│       ├── js/                    # JS modules
│       ├── css/                   # Styles
│       ├── icn/                   # Icons
│       └── vendor/                # Third-party libraries
├── tests/                         # Test suite
│   ├── unit/                      # Unit tests
│   ├── integration/               # Integration tests
│   ├── acceptance/                # BDD acceptance tests (Playwright)
│   ├── orm/                       # ORM model tests
│   └── features/                  # BDD feature files
├── devstart.py                    # Dev server entry point
├── tasks.py                       # Invoke tasks (lint/test/start)
├── pyproject.toml                 # Project config + dependencies
├── Dockerfile                     # Docker deployment
└── docker/                        # Docker helper scripts
```

---

## 4. Core Modules

### 4.1 Application Entry & Factory

#### [lute/main.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/main.py)
**Production entry point** using the `waitress` WSGI server.

| Function | Description |
|----------|-------------|
| `start()` | Main entry: parses CLI args (`--port`, `--local`, `--config`) |
| `_start(args)` | Configures and starts the app: load config → create app → init data → serve |
| `_get_config_file_path()` | Determines config file path (priority: specified > root config.yml > default) |
| `_create_prod_config_if_needed()` | Copies config template if config.yml doesn't exist |

#### [lute/app_factory.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/app_factory.py)
**Flask application factory**, the assembly center of the entire application.

| Function/Class | Description |
|----------------|-------------|
| `create_app()` | Main factory: load config → create dirs → init DB → create app → init parser plugins |
| `data_initialization()` | Post-start data init: check/load demo data → cleanup |
| `_create_app()` | Creates Flask instance, configures SQLAlchemy, registers all Blueprints and routes |
| `_add_base_routes()` | Registers base routes (home, refresh stats, clear demo, version, error handling, etc.) |
| `_setup_app_dirs()` | Creates data directory structure (backups, user images, audio, themes, temp, etc.) |
| `_init_parser_plugins()` | Loads parser plugins and creates data directories for each |

**Registered Blueprints:**
```python
language_bp, anki_bp, book_bp, bookmarks_bp, term_bp, termtag_bp,
read_bp, bing_bp, userimage_bp, useraudio_bp, termimport_bp,
backup_bp, settings_bp, themes_bp, stats_bp, cli_bp, tts_bp, dev_api_bp
```

**Key Middleware:**
- `_redirect_credentialed_url()`: Redirects URLs with BasicAuth to clean URLs (fixes sub-resource loading)
- `_before_request()`: Resets engine and settings after backup restore
- `_no_cache_dynamic_pages()`: Disables caching for `/read/` and `/term/` dynamic pages

#### [lute/config/app_config.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/config/app_config.py)

| Property | Description |
|----------|-------------|
| `env` | Runtime environment (`prod` / `dev`) |
| `is_docker` | Whether running in Docker |
| `dbname` | Database filename |
| `datapath` | User data directory |
| `dbfilename` | Full database file path |
| `plugin_datapath` | Plugin data directory |
| `userimagespath` | User images directory |
| `useraudiopath` | User audio directory |
| `userthemespath` | User themes directory |
| `temppath` | Temporary files directory |
| `system_backup_path` | System auto-backup directory |
| `default_user_backup_path` | Default user backup path |

---

### 4.2 Data Model Layer

#### [lute/models/book.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/models/book.py)

**Book** (`books` table) — Book entity

| Column | Type | Description |
|--------|------|-------------|
| `BkID` | SmallInteger PK | Primary key |
| `BkTitle` | String(200) | Book title |
| `BkLgID` | Integer FK → `languages.LgID` | Language |
| `BkSourceURI` | String(1000) | Source URL |
| `BkCurrentTxID` | Integer | Current reading Text page ID |
| `BkArchived` | Boolean | Archived flag |
| `BkAudioFilename` | String | Audio filename |
| `BkAudioCurrentPos` | Float | Audio current playback position |
| `BkAudioBookmarks` | String | Audio bookmarks JSON |
| `BkBookType` | String | Book type (`""`/`"youtube"`/`"bilibili"`/`"manga"`/`"mp3"`) |
| `BkSrtData` | String | Subtitle data JSON |
| `BkVideoCurrentPos` | Float | Video current playback position |
| `BkMangaPath` | String | Manga file path |
| `BkMangaData` | String | Mokuro manga JSON data |

Relations: `language` → Language, `texts` → Text[], `book_tags` → BookTag[]

**Text** (`texts` table) — Book pages

| Column | Type | Description |
|--------|------|-------------|
| `TxID` | Integer PK | Primary key |
| `TxBkID` | Integer FK → `books.BkID` | Parent book |
| `TxOrder` | Integer | Page order |
| `TxText` | String | Page text content |
| `TxWordCount` | Integer | Page word count |
| `TxStartDate` | DateTime | Reading start time |
| `TxReadDate` | DateTime | Reading completion time |

Relations: `book` → Book, `bookmarks` → TextBookmark[], `sentences` → Sentence[]

**Sentence** (`sentences` table) — Parsed sentences

| Column | Type | Description |
|--------|------|-------------|
| `SeID` | Integer PK | Primary key |
| `SeTxID` | Integer FK → `texts.TxID` | Parent text |
| `SeOrder` | Integer | Sentence order |
| `SeText` | Text | Sentence content (ZWS-delimited tokens) |
| `SeTextLC` | Text | Lowercased content (`*` means aligned with parser lowercase) |

**BookStats** (`bookstats` table) — Book statistics (view/materialized table)

**WordsRead** (`wordsread` table) — Reading event records

**TextBookmark** (`textbookmarks` table) — Page bookmarks

**BookTag** (`tags2` table) — Book tags

#### [lute/models/term.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/models/term.py)

**Term** (`words` table) — Term/word entity

| Column | Type | Description |
|--------|------|-------------|
| `WoID` | SmallInteger PK | Primary key |
| `WoLgID` | Integer FK → `languages.LgID` | Language |
| `WoText` | String(250) | Text (ZWS-delimited for multi-word terms) |
| `WoTextLC` | String(250) | Lowercased text |
| `WoStatus` | Integer | Mastery status (0=unknown, 1-5=learning, 98=ignore, 99=known) |
| `WoTranslation` | String(500) | Translation |
| `WoRomanization` | String(100) | Reading/pronunciation |
| `WoTokenCount` | Integer | Number of constituent tokens |
| `WoSyncStatus` | Boolean | Whether to sync status with parent |

Relations: `language` → Language, `term_tags` ↔ TermTag (many-to-many), `parents` ↔ Term (self-referencing many-to-many), `images` → TermImage[], `term_flash_message` → TermFlashMessage

**Status constants:**
- `Status.UNKNOWN = 0` — Unknown word
- `Status.WELLKNOWN = 99` — Well-known word
- `Status.IGNORED = 98` — Ignored word
- `Status.ALLOWED = [0, 1, 2, 3, 4, 5, 98, 99]` — Valid statuses

**Key methods:**

| Method | Description |
|--------|-------------|
| `Term.text.setter` | Auto-tokenizes, lowercases, and counts tokens on set; existing terms cannot modify text content |
| `Term.create_term_no_parsing()` | Creates a term without re-parsing (for already-parsed page scenarios) |
| `_parse_string_add_zws()` | Separates tokenized text with zero-width spaces |
| `add_parent()` / `remove_all_parents()` | Manages parent-child relationships (for inflections: verb root → conjugated form) |
| `get_flash_message()` / `pop_flash_message()` | Flash message get/consume |
| `set_current_image()` / `get_current_image()` | Manages term images |

#### [lute/models/language.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/models/language.py)

**Language** (`languages` table) — Language entity

| Column | Type | Description |
|--------|------|-------------|
| `LgID` | SmallInteger PK | Primary key |
| `LgName` | String(40) | Language name |
| `LgCharacterSubstitutions` | String(500) | Character substitution rules |
| `LgRegexpSplitSentences` | String(500) | Sentence splitting regex |
| `LgExceptionsSplitSentences` | String(500) | Sentence splitting exceptions |
| `LgRegexpWordCharacters` | String(500) | Word character set |
| `LgRightToLeft` | Boolean | Right-to-left rendering |
| `LgShowRomanization` | Boolean | Show reading/pronunciation |
| `LgParserType` | String(20) | Parser type |
| `LgIsActive` | Boolean | Active/frozen |
| `LgKiwiTokenizerMode` | String(20) | Korean Kiwi tokenizer mode |
| `LgKiwiStemming` | Boolean | Korean stemming |
| `LgKiwiFilterParticles` | Boolean | Korean particle filtering |
| `LgKiwiJoinCompoundNouns` | Boolean | Korean compound noun joining |

**LanguageDictionary** (`languagedicts` table) — Language dictionary configuration

| Method | Description |
|--------|-------------|
| `parser` | Property, gets the parser instance |
| `is_supported` | Checks if parser is available |
| `get_parsed_tokens()` | Tokenizes text |
| `get_lowercase()` | Gets lowercased text |
| `active_dict_uris()` | Gets active dictionary URI list |
| `to_dict()` / `from_dict()` | Serialization/deserialization (for predefined language definitions) |

#### [lute/models/setting.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/models/setting.py)

**SettingBase** (`settings` table) — Settings base table (polymorphic)
- `StKey` (String PK) — Key name
- `StKeyType` (String) — Type identifier (`"user"` / `"system"`)
- `StValue` (String) — Value

**UserSetting** — User settings (polymorphic identity = "user")
**SystemSetting** — System settings (polymorphic identity = "system")
**BackupSettings** — Backup settings convenience wrapper

#### [lute/models/repositories.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/models/repositories.py)

Repository pattern wrappers providing database query abstraction.

| Class | Key Methods | Description |
|-------|-------------|-------------|
| `SettingRepositoryBase` | `set_value()`, `get_value()`, `key_exists()`, `delete_key()` | Settings CRUD base |
| `UserSettingRepository` | Inherits + `get_backup_settings()`, `set_dynamic_value()` | User settings repository |
| `SystemSettingRepository` | Inherits | System settings repository |
| `LanguageRepository` | `find()`, `find_by_name()`, `all_dictionaries()` | Language queries |
| `TermTagRepository` | `find()`, `find_by_text()`, `find_or_create_by_text()` | Tag queries |
| `TermRepository` | `find()`, `find_by_spec()`, `delete_empty_images()` | Term queries |
| `BookTagRepository` | `find_or_create_by_text()` | Book tags |
| `BookRepository` | `find()`, `find_by_title()` | Book queries |

---

### 4.3 Reading Module

#### [lute/read/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/read/routes.py)

The reading module is Lute's core user interface, responsible for displaying tokenized text, handling user clicks, editing terms, and managing reading progress.

| Route | Method | Handler | Description |
|-------|--------|---------|-------------|
| `/read/<int:bookid>` | GET | `read()` | Open book at current reading position |
| `/read/<int:bookid>/page/<int:pagenum>` | GET | `read_page()` | Open specific page |
| `/read/<int:bookid>/peek/<int:pagenum>` | GET | `peek_page()` | Peek page (doesn't update reading progress) |
| `/read/page_done` | POST | `page_done()` | Mark page as complete, record reading event |
| `/read/delete_page/<int:bookid>/<int:pagenum>` | GET | `delete_page()` | Delete page |
| `/read/new_page/<int:bookid>/<position>/<int:pagenum>` | GET/POST | `new_page()` | Create new page |
| `/read/save_player_data` | POST | `save_player_data()` | Save audio player position and bookmarks |
| `/read/save_youtube_player_data` | POST | `save_youtube_player_data()` | Save YouTube video position |
| `/read/bilibili/stream/mpd/<bvid>` | GET | `bilibili_mpd()` | Get Bilibili DASH manifest (MPD), BaseURLs pointing to local proxy |
| `/read/bilibili/stream/proxy/<bvid>/<stream_type>` | GET | `bilibili_proxy()` | Proxy Bilibili CDN byte-range requests |
| `/read/youtube_subtitle_words/<int:bookid>` | GET | `youtube_subtitle_words()` | Lazy-loaded subtitle word HTML (cached) |
| `/read/start_reading/<int:bookid>/<int:pagenum>` | GET | `start_reading()` | AJAX start reading, returns page content |
| `/read/update_start_date/<int:bookid>/<int:pagenum>` | GET/POST | `update_start_date()` | Update start time via sendBeacon |
| `/read/refresh_page/<int:bookid>/<int:pagenum>` | GET | `refresh_page()` | Refresh page content |
| `/read/empty` | GET | `empty()` | Empty page |
| `/read/termform/<int:langid>/<text>` | GET/POST | `term_form()` | Multi-word term creation form |
| `/read/edit_term/<int:term_id>` | GET/POST | `edit_term_form()` | Edit term |
| `/read/term_bulk_edit_form` | GET | `term_bulk_edit_form()` | Bulk term edit form |
| `/read/termpopup/<int:termid>` | GET | `term_popup()` | Get term popup HTML |
| `/read/flashcopied` | GET | `flashcopied()` | Flash card copy success page |
| `/read/editpage/<int:bookid>/<int:pagenum>` | GET/POST | `edit_page()` | Edit page text content |

**Key mechanisms:**
- `_yt_subtitle_words_cache`: Module-level cache for subtitle tokenization results, avoiding repeated parsing on each page load
- `_render_book_page()`: Core rendering function, selects template based on book type (normal/YouTube/Bilibili/MP3/manga)

#### [lute/read/service.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/read/service.py)

| Class/Method | Description |
|--------------|-------------|
| **Service** | Reading service |
| `start_reading()` | Start reading: set text start time, get paragraphs, save new status-0 terms |
| `get_paragraphs()` | Get paragraphs (no reading progress tracking) |
| `mark_page_read()` | Mark page as read, record WordsRead, optionally set unknown words to known |
| `update_start_date()` | Lightweight start time update |
| `set_unknowns_to_known()` | Mark all unknown words on page as known |
| `bulk_status_update()` | Bulk term status update |
| `manga_page_context()` | Build manga page rendering context (image URLs + text blocks + tokenization) |
| `get_popup_data()` | Build term popup data (translation, reading, image, parents, components) |
| **TermPopup** | Popup data object |
| `show` | Whether there's content to display |
| `term_and_parents_text()` | Term text + parent term text |

#### [lute/read/render/service.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/read/render/service.py)

**RenderService** — Text rendering engine

| Method | Description |
|--------|-------------|
| `get_textitems()` | Get TextItem array for text (tokenize → find terms → calculate TextItems) |
| `get_paragraphs()` | Get paragraph-sentence-segmented TextItem 2D array |
| `find_all_Terms_in_string()` | Find all matching terms in a string |
| `get_multiword_indexer()` | Build multi-word indexer (ahocorapy) |
| `_find_all_terms_in_tokens()` | Find all matching terms in already-tokenized tokens |

#### [lute/read/render/text_item.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/read/render/text_item.py)

**TextItem** — The smallest rendering unit

| Property | Description |
|----------|-------------|
| `text` | Original text |
| `text_lc` | Lowercased text |
| `is_word` | Whether it's a word |
| `token_count` | Original token count |
| `display_count` | Display token count (handles overlap) |
| `sentence_number` | Sentence number |
| `paragraph_number` | Paragraph number |
| `term` | Associated Term object |
| `wo_status` | Term status |
| `display_text` | Actual displayed text (handles overlapping parts) |
| `html_display_text` | Text rendered to browser |
| `span_id` | HTML span ID |
| `status_class` | CSS status class name |
| `html_class_string` | Full CSS class string |

#### [lute/read/render/calculate_textitems.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/read/render/calculate_textitems.py)

**Core Algorithm — TextItem Overlap Calculation**

This is the most critical algorithm in the reading renderer. When text contains multiple overlapping terms (single words and multi-word phrases), it determines which TextItems to actually display.

**Algorithm steps:**
1. Create word TextItems for all tokens
2. Create TextItems for all matching multi-word terms
3. Sort by (start index, -token_count)
4. Write TextItem IDs into output array from back to front
5. Calculate display_count for each ID
6. Keep only TextItems with display_count > 0

**Example:**
```
Token:      A   B   C   D   E   F   G   H   I
Terms:      A   BC  CDE     EFGHI  FG
Output:     [A] [BC] [-D E] [-F G H I]
```

#### [lute/read/render/multiword_indexer.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/read/render/multiword_indexer.py)

**MultiwordTermIndexer** — Efficient multi-word term matching using `ahocorapy`

| Method | Description |
|--------|-------------|
| `add()` | Add term to keyword tree |
| `search_all()` | Search for all matching multi-word terms in token sequence |

#### [lute/read/bilibili_stream.py](file:///Users/cxi/Documents/lutedev/lute-v3/lute/read/bilibili_stream.py)

**Bilibili DASH Stream Proxy** — Bypasses the official player's domain whitelist restriction

Bilibili's official iframe player won't initialize on non-whitelisted domains (shows cover but never loads the video). This module bypasses the player entirely and plays the raw DASH stream (fragmented MP4 video + audio):

| Function | Description |
|----------|-------------|
| `stream_info(bvid, page)` | Calls Bilibili's public `view`/`playurl` APIs to get segment URLs and codec metadata (30-min cache) |
| `build_mpd(info, video_proxy, audio_proxy)` | Generates on-demand DASH manifest (MPD) with BaseURLs pointing to local proxy endpoints |
| `proxy_stream(base_url, range_header)` | Relays dash.js Range requests to Bilibili CDN, adding Referer/UA headers to bypass CORS and anti-leeching |
| `invalidate_stream(bvid)` | Clears stream cache when a book is re-imported |
| `_fetch_view()` / `_fetch_playurl()` | Calls Bilibili public APIs for video info and play URLs |
| `_pick(streams)` | Selects the highest-bandwidth stream from DASH stream list |

---

### 4.4 Parsing Module

#### [lute/parse/base.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/parse/base.py)

**ParsedToken** — Tokenization result

| Property | Description |
|----------|-------------|
| `token` | Token text |
| `is_word` | Whether it's a word |
| `is_end_of_sentence` | Whether it ends a sentence |
| `is_end_of_paragraph` | Whether it ends a paragraph |
| `order` | Global order number |
| `sentence_number` | Sentence number |

**AbstractParser** — Abstract base class for parsers

| Method | Description |
|--------|-------------|
| `get_parsed_tokens(text, language)` | **Abstract**: Tokenize text |
| `get_reading(text)` | Get reading/pronunciation (optional) |
| `get_lemma(text)` | Get dictionary form (optional) |
| `get_lowercase(text)` | Get lowercase (overridable, e.g., for Turkish) |
| `is_supported()` | Check if parser is available |
| `name()` | **Abstract**: Parser name |
| `languages()` | Return applicable language list, or None (generic) |

#### [lute/parse/registry.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/parse/registry.py)

**Parser Registry** — Manages all available parsers

| Method | Description |
|--------|-------------|
| `init_parser_plugins()` | Load plugin parsers (discovered via `entry_points`) |
| `get_parser(parser_name)` | Get parser instance by name |
| `is_supported(parser_name)` | Check if parser is available |
| `supported_parsers()` | List all available parsers |
| `supported_parser_types()` | List all available parser type names |

**Built-in parser registry:**

| Key | Class | Description | Languages |
|-----|-------|-------------|-----------|
| `spacedel` | `SpaceDelimitedParser` | Space-delimited generic parser | English, French, Spanish, etc. |
| `turkish` | `TurkishParser` | Turkish special handling | Turkish |
| `japanese` | `JapaneseParser` | Japanese MeCab parser | Japanese |
| `japanese_sudachi` | `JapaneseSudachiParser` | Japanese Sudachi parser | Japanese |
| `korean` | `KoreanParser` | Korean Kiwi parser | Korean |
| `classicalchinese` | `ClassicalChineseParser` | Classical Chinese character-by-character parser | Classical Chinese |

#### [lute/parse/space_delimited_parser.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/parse/space_delimited_parser.py)

**SpaceDelimitedParser** — Generic space-delimited language parser

| Method | Description |
|--------|-------------|
| `get_parsed_tokens()` | Main entry: clean text → `_parse_to_tokens()` |
| `_parse_to_tokens()` | Apply character substitutions, chunk by paragraph, each chunk calls `parse_para()` |
| `parse_para()` | Uses language's `word_characters` regex to extract words, `split_sentences` for sentence splitting |
| `preg_match_capture()` | Regex match capture with position info |
| `get_default_word_characters()` | Gets default Unicode word character set (covers all language letter categories) |
| `get_default_regexp_split_sentences()` | Gets default sentence-splitting regex (Unicode end-of-sentence punctuation) |

**TurkishParser** — Extends SpaceDelimitedParser, overrides `get_lowercase()` for Turkish special i casing rules.

#### [lute/parse/mecab_parser.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/parse/mecab_parser.py)

**JapaneseParser** — Japanese MeCab parser (using `natto-py`)

| Method | Description |
|--------|-------------|
| `is_supported()` | Checks if MeCab is available, auto-detects path |
| `get_parsed_tokens()` | Parses text with MeCab, distinguishes words/non-words/sentence-end |
| `get_reading()` | Gets reading (supports IPADIC and Unidic, outputs hiragana/katakana/alphabet) |
| `get_lemma()` | Gets dictionary form (辞書形), auto-skips particle/auxiliary verb suffixes |
| `_detect_dict_type()` | Auto-detects dictionary type (IPADIC/Unidic) |
| `_is_jiritsugo()` | Checks if a token is a content word (自立語) |
| `_normalize_unidic_lemma()` | Normalizes Unidic dictionary form (e.g., `為る` → `する`) |
| `_clean_lemma_value()` | Cleans POS classification info from Unidic lemma |

**Key mechanisms:**
- MeCab instance caching: avoids repeated initialization (~18ms each)
- Supports both IPADIC and Unidic dictionary formats
- Auto-detects dictionary path (multi-architecture Docker support)
- Controlled by `japanese_dict` and `japanese_reading` user settings

#### [lute/parse/kiwi_parser.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/parse/kiwi_parser.py)

**KoreanParser** — Korean Kiwi parser (using `kiwipiepy`)

Based on the Kiwi tokenizer, supports configurable tokenizer mode, stemming, particle filtering, and compound noun joining.

#### [lute/parse/sudachi_parser.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/parse/sudachi_parser.py)

**JapaneseSudachiParser** — Japanese Sudachi parser (using `sudachipy`)

An alternative tokenization option, with configurable split mode (A/B/C), reading and dictionary form extraction.

#### [lute/parse/character_parser.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/parse/character_parser.py)

**ClassicalChineseParser** — Classical Chinese character-by-character parser

Each character is treated as an independent token, suitable for Classical Chinese and other languages without spaces.

---

### 4.5 Book Management Module

#### [lute/book/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/book/routes.py)

| Route | Method | Description |
|-------|--------|-------------|
| `/book/datatables/active` | POST | Active books data source |
| `/book/datatables/Archived` | POST | Archived books data source |
| `/book/archived` | GET | Archived books page |
| `/book/new` | GET/POST | Create new book (supports TXT/EPUB/PDF/SRT/VTT upload) |
| `/book/edit/<int:bookid>` | GET/POST | Edit book |
| `/book/import_webpage` | GET/POST | Import from URL (web page, YouTube, Bilibili, MP3) |
| `/book/archive/<int:bookid>` | POST | Archive book |
| `/book/unarchive/<int:bookid>` | POST | Unarchive book |
| `/book/delete/<int:bookid>` | POST | Delete book |
| `/book/table_stats/<int:bookid>` | GET | Single book stats |
| `/book/table_stats` | POST | Batch stats update |

**Internal dispatch functions:**
- `_import_youtube_video()`: Handles YouTube video import (with subtitle parsing)
- `_import_bilibili_video()`: Handles Bilibili video import (URL + SRT/VTT subtitle file, title fetched via view API)
- `_import_mp3_audio()`: Handles MP3 audio import (with subtitle parsing)
- `_import_mokuro_manga()`: Handles Mokuro manga import (zip/cbz extraction + .mokuro JSON parsing)

#### [lute/book/service.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/book/service.py)

**FileTextExtraction** — File text extraction

| Method | Description |
|--------|-------------|
| `get_file_content()` | Selects parser by file extension, returns text content |
| `_get_textfile_content()` | Reads UTF-8 text file |
| `_get_epub_content()` | Parses EPUB using `openepub` |
| `_get_pdf_content()` | Parses PDF using `pypdf` |
| `_get_srt_content()` | Parses SRT subtitle file |
| `_get_vtt_content()` | Parses WebVTT subtitle file |

**Service** — Book import service

| Method | Description |
|--------|-------------|
| `import_book()` | Main book import entry: processes text/audio/manga files, saves to database |
| `book_data_from_url()` | Extracts web page content from URL (using BeautifulSoup) |
| `youtube_title()` | Gets YouTube video title via oEmbed API |
| `bilibili_title()` | Gets Bilibili video title via view API (no API key required) |
| `save_audio_file()` | Saves audio file to user audio directory |
| `extract_manga()` | Extracts Mokuro manga zip/cbz, parses .mokuro JSON |

**Core functions:**
- `youtube_video_id()`: Extracts YouTube video ID from URL
- `bilibili_video_id()`: Extracts Bilibili video ID from URL (returns `(bvid, aid)` tuple, supports both BV and av formats)
- `bilibili_page()`: Extracts episode number (`?p=N`, default 1)
- `bilibili_embed_url()`: Builds Bilibili player embed URL
- `parse_subtitle_file()` / `parse_subtitle_content()`: Parses SRT/VTT subtitle files
- `cues_to_srt_text()`: Converts subtitle cues to SRT format

#### [lute/book/model.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/book/model.py)

**Repository** — Book business object repository

| Method | Description |
|--------|-------------|
| `new_book()` | Creates new Book business object |
| `load()` | Loads Book from database |
| `add()` | Adds Book to database |
| `delete()` | Deletes Book |

#### [lute/book/stats.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/book/stats.py)

**Service** — Book statistics service

| Method | Description |
|--------|-------------|
| `refresh_stats()` | Refreshes all book statistics |
| `mark_stale()` | Marks book statistics as stale |
| `get_stats()` | Gets single book stats |
| `get_stats_batch()` | Gets batch stats |

---

### 4.6 Term Management Module

#### [lute/term/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/term/routes.py)

| Route | Method | Description |
|-------|--------|-------------|
| `/term/index` | GET | Term list page |
| `/term/datatables` | POST | Term DataTables data source |
| `/term/new` | GET/POST | Create new term |
| `/term/edit/<int:termid>` | GET/POST | Edit term |
| `/term/delete/<int:termid>` | POST | Delete term |
| `/term/search` | GET | Search terms |
| `/term/apply_ajax_update` | POST | AJAX update term (translation, status, tags, etc.) |
| `/term/references/<int:termid>` | GET | Term references list |
| `/term/bulk_update` | POST | Bulk term status update |

#### [lute/term/model.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/term/model.py)

**Term** (Business Object) — Term business object

| Property | Description |
|----------|-------------|
| `id` | Database ID |
| `language_id` | Language ID |
| `text` | Text |
| `text_lc` | Lowercased text |
| `translation` | Translation |
| `romanization` | Reading/pronunciation |
| `status` | Mastery status |
| `term_tags` | Tag list |
| `parents` | Parent term list |
| `current_image` | Current image |
| `flash_message` | Flash message |
| `sync_status` | Whether to sync status |

**Repository** — Term business object repository

| Method | Description |
|--------|-------------|
| `find(langid, text)` | Find term, returns None if not found |
| `find_or_new(langid, text)` | Find or create new term |
| `find_matches(langid, text)` | Fuzzy match search |
| `load(term_id)` | Load by ID |
| `add(term)` | Add to database session |
| `delete(term)` | Mark for deletion |
| `commit()` | Commit and clear identity map |

**Key mechanism — Parent-child sync:**
- When `sync_status = True` and there's exactly one parent, child and parent status are auto-synced
- When setting a parent, if the parent doesn't exist or has status 0, it inherits the child's status, translation, and image

**ReferencesRepository** — Term reference query

| Method | Description |
|--------|-------------|
| `find_references(term)` | Finds all references for the term, its children, and parents |
| `find_references_by_id(term_id)` | Finds references by ID |

Reference queries use `LIKE` matching on the `sentences` table's `SeText` field, with highlighting support for matched terms in context.

---

### 4.7 Database Module

#### [lute/db/__init__.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/db/__init__.py)
```python
db = SQLAlchemy()
```
Singleton, used globally.

#### [lute/db/setup/main.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/db/setup/main.py)

**Setup** — Database initialization flow

| Step | Description |
|------|-------------|
| 1 | Check if database file exists |
| 2 | If not, create baseline from `baseline.sql` |
| 3 | Check for pending migrations |
| 4 | If migrations exist, backup database first |
| 5 | Run all pending migration SQL scripts |

**BackupManager** — Database backup manager

| Method | Description |
|--------|-------------|
| `do_backup()` | Creates gzip-compressed backup, cleans up old backups |

#### [lute/db/setup/migrator.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/db/setup/migrator.py)

**SqliteMigrator** — SQLite migration engine

| Method | Description |
|--------|-------------|
| `do_migration()` | Runs all migrations (one-way first, then repeatable) |
| `has_migrations()` | Checks for pending migrations |
| `_get_pending()` | Gets list of unapplied migration files |
| `_process_folder()` | Runs and records one-way migrations to `_migrations` table |
| `_process_repeatable()` | Runs repeatable migrations (always runs) |

**Migration mechanism:**
- Migration files named: `YYYYMMDD_HHMMSS_description.sql`
- Executed migrations recorded in `_migrations` table
- Repeatable migrations (e.g., triggers) always execute

#### [lute/db/management.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/db/management.py)

| Function | Description |
|----------|-------------|
| `add_default_user_settings()` | Initializes default user settings (backup, TTS, Anki, theme, etc.) |
| `delete_all_data()` | Dangerous: deletes all data, resets user settings |
| `_revised_mecab_path()` | Auto-corrects MeCab library path (multi-architecture Docker support) |

#### [lute/db/demo.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/db/demo.py)

**DemoService** — Demo data management

| Method | Description |
|--------|-------------|
| `should_load_demo_data()` | Checks whether to load demo data |
| `contains_demo_data()` | Checks if demo data exists |
| `load_demo_data()` | Loads demo languages and stories |
| `load_demo_languages()` | Loads 13 predefined languages |
| `load_demo_stories()` | Loads stories for each language |
| `delete_demo_data()` | Deletes demo data |
| `tutorial_book_id()` | Gets tutorial book ID |

---

### 4.8 Language Management Module

#### [lute/language/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/language/routes.py)

| Route | Method | Description |
|-------|--------|-------------|
| `/language/index` | GET | Language list page |
| `/language/list_predefined` | GET | Predefined language list |
| `/language/new` | GET/POST | Create new language (supports importing from predefined) |
| `/language/edit/<int:langid>` | GET/POST | Edit language configuration |
| `/language/delete/<int:langid>` | POST | Delete language |
| `/language/activate/<int:langid>` | POST | Activate language |
| `/language/deactivate/<int:langid>` | POST | Freeze/deactivate language |

#### [lute/language/service.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/language/service.py)

| Method | Description |
|--------|-------------|
| `get_language_def(name)` | Loads language definition from predefined YAML file |
| `load_language(lang_def)` | Imports language definition into database |
| `get_available_language_names()` | Gets all available predefined language names |

**Predefined language definition files:** `lute/db/language_defs/`, one subdirectory per language with `definition.yaml` and story text.

---

### 4.9 Settings Module

#### [lute/settings/current.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/settings/current.py)

**Global settings cache mechanism**

```python
current_settings = {}  # Global key-value cache
current_hotkeys = {}   # Hotkey mapping
```

| Function | Description |
|----------|-------------|
| `refresh_global_settings(session)` | Reloads all settings from DB into cache, converts booleans |

**Auto-converted boolean keys:** `open_popup_in_new_tab`, `stop_audio_on_term_form_open`, `show_highlights`, `term_popup_promote_parent_translation`, `term_popup_show_components`, `use_ankiconnect`, `show_streak_on_home`, `tts_hover_pronunciation`, `tts_click_pronunciation`, `tts_show_control_panel`, `tts_show_sentence_buttons`

#### [lute/settings/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/settings/routes.py)

| Route | Method | Description |
|-------|--------|-------------|
| `/settings/index` | GET/POST | Settings page |
| `/settings/shortcuts` | GET/POST | Hotkey settings |

---

### 4.10 TTS & Translation

#### [lute/tts/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/tts/routes.py)

This module provides Edge-TTS speech synthesis and Google/MyMemory auto-translation.

| Route | Method | Description |
|-------|--------|-------------|
| `/tts/<lang>/<path:text>` | GET | Generate and return MP3 audio for specified language and text |
| `/api/translate/<sl>/<tl>/<path:text>` | GET | Return translation JSON |

**Key data:**

`VOICE_MAP` — 30+ language Edge-TTS voice mappings

`LANG_NAME_TO_CODE` — 40+ language name to ISO 639-1 code mapping

**Core functions:**

| Function | Description |
|----------|-------------|
| `tts_speak()` | Speech synthesis: MD5 cache check → Edge-TTS generation → return MP3 |
| `_generate_audio()` | Async call to Edge-TTS `Communicate.save()` |
| `translate()` | Translation: in-memory cache → Google API → MyMemory fallback |
| `_translate_via_google()` | Calls Google Translate API |
| `_translate_via_mymemory()` | Calls MyMemory API (fallback) |
| `get_lang_code()` | Gets ISO language code from language name |

**Cache mechanism:**
- Audio cache: `DATAPATH/tts_cache/` directory, MD5-based filenames
- Translation cache: in-memory dict `trans_cache`

---

### 4.11 Anki Export Module

#### [lute/ankiexport/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/ankiexport/routes.py)

| Route | Method | Description |
|-------|--------|-------------|
| `/ankiexport/index` | GET/POST | Export spec list |
| `/ankiexport/spec/new` | GET/POST | Create new export spec |
| `/ankiexport/spec/edit/<int:spec_id>` | GET/POST | Edit export spec |
| `/ankiexport/spec/delete/<int:spec_id>` | GET/POST | Delete export spec |
| `/ankiexport/get_card_post_data` | POST | Get AnkiConnect request data |
| `/ankiexport/validate_export_specs` | POST | Validate export specs |

#### [lute/ankiexport/service.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/ankiexport/service.py)

| Method | Description |
|--------|-------------|
| `get_ankiconnect_post_data()` | Builds AnkiConnect request JSON |
| `validate_specs()` | Validates all export specs |
| `evaluate_criteria()` | Filters terms based on criteria |
| `get_values_and_media_mapping()` | Applies field mapping to terms |

#### [lute/ankiexport/criteria.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/ankiexport/criteria.py)

Filter criteria parser supporting: tag filter, language filter, status filter, parent tag filter, and combined conditions.

---

### 4.12 Backup & Restore Module

#### [lute/backup/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/backup/routes.py)

| Route | Method | Description |
|-------|--------|-------------|
| `/backup/index` | GET | Backup list page |
| `/backup/backup` | GET | Backup form page |
| `/backup/do_backup` | POST | AJAX execute backup |
| `/backup/skip_this_backup` | GET | Skip current backup |
| `/backup/restore` | POST | Upload file to restore backup |
| `/backup/restore_file/<filename>` | POST | Restore from backup directory |
| `/backup/download/<filename>` | GET | Download backup file |

#### [lute/backup/service.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/backup/service.py)

| Method | Description |
|--------|-------------|
| `create_backup()` | Creates timestamped gzip backup |
| `restore_backup()` | Restores database from backup file |
| `list_backups()` | Lists all backup files |
| `should_run_auto_backup()` | Determines if auto-backup should run |
| `backup_warning()` | Gets backup warning info |

---

### 4.13 Frontend Module

#### JavaScript Files Overview

| File | Description |
|------|-------------|
| [lute.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/lute.js) | Main frontend logic: page rendering, AJAX requests, popup management, term editing, TTS/audio/video player initialization |
| [tts.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/tts.js) | TTS audio playback control, cache management, player integration |
| [player.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/player.js) | Audio player (play, pause, seek, bookmarks, rate control) |
| [lute-anki.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/lute-anki.js) | Anki export: term selection, export config, AnkiConnect requests |
| [lute-popups.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/lute-popups.js) | Popup interactions (modal open/close/dynamic loading) |
| [youtube-player.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/youtube-player.js) | YouTube player integration (IFrame API) |
| [bilibili-player.js](file:///Users/cxi/Documents/lutedev/lute-v3/lute/static/js/bilibili-player.js) | Bilibili player integration (dash.js direct DASH stream, reuses same UI/scrolling subtitle/loop controls as YouTube) |
| [dict-tabs.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/dict-tabs.js) | Dictionary tab management |
| [text-options.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/text-options.js) | Text display options (font size, line height, column count) |
| [resize.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/resize.js) | Component resize adaptation |
| [lute-hotkey-utils.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/lute-hotkey-utils.js) | Keyboard shortcut support |
| [lute-tagify-utils.js](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/static/js/lute-tagify-utils.js) | Tagify tag input component integration |

#### Theme CSS

Built-in themes in [lute/themes/css/](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/themes/css/):
`Default.css`, `Default-Dark.css`, `Dark_slate.css`, `Night.css`, `Dusk.css`, `Apple_Books.css`, `Black_and_White.css`, `LWT.css`, `LingQ.css`

---

### 4.14 Other Auxiliary Modules

#### [lute/stats/](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/stats/)

Reading statistics module, using Chart.js for visualization.

| Route | Description |
|-------|-------------|
| `/stats/` | Stats main page |
| `/stats/data` | Reading data JSON (for charts) |
| `/stats/term_data` | Term statistics JSON |

**Service.get_reading_streak()** — Calculates consecutive reading days

#### [lute/bing/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/bing/routes.py)

Bing image search, supports saving images to user directory.

| Route | Description |
|-------|-------------|
| `/bing/search_page/<langid>/<text>/<searchstring>` | Image search page |
| `/bing/search/<langid>/<text>/<searchstring>` | Execute search |
| `/bing/save` | Save image |

#### [lute/userimage/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/userimage/routes.py)

| Route | Description |
|-------|-------------|
| `/userimages/<int:lgid>/<path:f>` | GET Serve user images |

#### [lute/useraudio/routes.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/useraudio/routes.py)

| Route | Description |
|-------|-------------|
| `/useraudio/stream/<int:bookid>` | GET Stream user audio |

#### [lute/cli/commands.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/cli/commands.py)

Flask CLI commands, invoked via `flask cli <command>`.

| Command | Description |
|---------|-------------|
| `cli hello` | Test command |
| `cli language_export` | Export language terms |
| `cli book_term_export` | Export book terms |
| `cli import_books_from_csv` | Batch import books from CSV |

#### [lute/utils/data_tables.py](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/utils/data_tables.py)

DataTables SQL query builder, supporting server-side pagination, sorting, and search.

#### [lute/themes/](file:///Users/cxi/.trae-cn/worktrees/lute-v3/feat-generate-code-wiki-ovD7fM/lute/themes/)

Theme management module, supporting theme switching, custom CSS, and user-uploaded themes.

---

## 5. Data Models

### ER Diagram Core Relationships

```
Language (languages)
  ├── Book (books)          ──────── has_many Text (texts)
  │     └── Text (texts)    ──────── has_many Sentence (sentences)
  │     └── Text            ──────── has_many TextBookmark (textbookmarks)
  │     └── BookStats (bookstats)   ── Materialized view
  ├── Term (words)          ──────── has_many TermImage (wordimages)
  │     └── Term            ──────── has_many self parents (wordparents)
  │     └── Term            ──────── has_many TermFlashMessage (wordflashmessages)
  │     └── TermTag (tags)  ──────── many-to-many (wordtags)
  ├── LanguageDictionary (languagedicts)
  └── Status (statuses)     ──────── status definitions

Settings (settings) ──── polymorphic: UserSetting / SystemSetting

WordsRead (wordsread) ── reading event records
```

### Key Junction Tables

| Table | Purpose | Relationship |
|-------|---------|-------------|
| `wordparents` | Term parent-child (inflection) | words.WoID ← → words.WoID |
| `wordtags` | Term-tag many-to-many | words.WoID ↔ tags.TgID |
| `booktags` | Book-tag many-to-many | books.BkID ↔ tags2.T2ID |

### Term Status Machine

```
0 (Unknown) ─→ 1 ─→ 2 ─→ 3 ─→ 4 ─→ 5 ─→ 99 (Well Known)
                                            98 (Ignored)
```

Status 0 is auto-created for unknown words; users progressively increase status through reading interaction.

---

## 6. Route Overview

### Base Routes (app_factory.py)

| Path | Method | Description |
|------|--------|-------------|
| `/` | GET | Home page |
| `/refresh_all_stats` | GET/POST | Refresh all stats |
| `/wipe_database` | GET | Clear demo data |
| `/remove_demo_flag` | GET | Exit demo mode |
| `/version` | GET | Version info page |
| `/info` | GET | JSON info API |
| `/static/js/never_cache/<path:filename>` | GET | Cached JS files |
| `/sw.js` | GET | PWA Service Worker |
| `/manifest.webmanifest` | GET | PWA Manifest |

### Blueprint Route Summary

| Blueprint | Prefix | Routes | Key Features |
|-----------|--------|--------|--------------|
| `read` | `/read` | 21 | Reading pages, term editing, player controls, Bilibili stream proxy |
| `book` | `/book` | 11 | Book CRUD, import (including Bilibili), stats |
| `term` | `/term` | 10 | Term CRUD, search, batch operations |
| `language` | `/language` | 7 | Language CRUD, activate/freeze |
| `settings` | `/settings` | 2 | User settings, hotkeys |
| `backup` | `/backup` | 7 | Backup, restore, download |
| `ankiexport` | `/ankiexport` | 6 | Anki export spec management |
| `tts` | (no prefix) | 2 | TTS synthesis, translation |
| `stats` | `/stats` | 3 | Reading stats, chart data |
| `themes` | `/theme` | 7 | Theme switching, customization, upload |
| `bing` | `/bing` | 4 | Image search, save |
| `userimage` | `/userimages` | 1 | Image serving |
| `useraudio` | `/useraudio` | 1 | Audio streaming |
| `bookmarks` | `/bookmarks` | 1 | Bookmark list |
| `termtag` | `/termtag` | 4 | Tag CRUD |
| `termimport` | `/termimport` | 1 | Term import |
| `term_parent_map` | `/term_parent_map` | 1 | Parent-child relationship view |
| `cli` | (Flask CLI) | 4 | CLI tools |
| `dev_api` | `/dev_api` | (testing only) | Test API |

---

## 7. Dependencies

### Core Dependencies (pyproject.toml)

| Package | Purpose |
|---------|---------|
| `Flask-SQLAlchemy>=3.1.1` | ORM framework |
| `Flask-WTF>=1.2.1` | Form handling |
| `natto-py>=1.0.1` | MeCab Japanese tokenization Python binding |
| `jaconv>=0.3.4` | Japanese kana conversion |
| `platformdirs>=3.10.0` | Cross-platform data directories |
| `requests>=2.31.0` | HTTP requests (translation API, web import) |
| `beautifulsoup4>=4.12.2` | HTML parsing (web import) |
| `PyYAML>=6.0.1` | YAML config parsing |
| `waitress>=2.1.2` | Production WSGI server |
| `openepub>=0.0.9` | EPUB parsing |
| `pypdf>=3.17.4` | PDF parsing |
| `subtitle-parser>=1.3.0` | SRT subtitle parsing |
| `ahocorapy>=1.6.2` | Multi-word term matching algorithm |
| `edge-tts>=6.1.0` | Microsoft Edge TTS speech synthesis |

### Optional Dependencies

| Package | Purpose |
|---------|---------|
| `sudachipy + sudachidict-core` | Japanese Sudachi tokenization |
| `kiwipiepy>=0.15.0` | Korean Kiwi tokenization |

### Development Dependencies

| Package | Purpose |
|---------|---------|
| `playwright` | Browser automation testing |
| `pytest-bdd` | BDD test framework |
| `pylint` | Code linting |
| `black` | Code formatting |
| `coverage` | Test coverage |
| `pre-commit` | Git hooks |

---

## 8. Running the Project

### Environment Requirements

- Python >= 3.8
- SQLite 3 (system built-in)
- (Optional) MeCab Japanese tokenization library
- (Optional) Kiwi Korean tokenization library

### Quick Start (Development Mode)

```bash
# 1. Clone the project
git clone https://github.com/luteorg/lute-v3.git
cd lute-v3

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or venv\Scripts\activate  # Windows

# 3. Install dependencies
pip install -e .

# 4. Configure
# Edit lute/config/config.yml or use default config
# Default uses lute/config/config.yml file

# 5. Start development server
python devstart.py --port 5001
# or using Invoke
inv start --port 5001
```

### Production Deployment

```bash
# Use production entry point
python -m lute.main --port 5001

# Or specify config file
python -m lute.main --config /path/to/config.yml --port 5001

# Local-only access
python -m lute.main --local --port 5001
```

### Docker Deployment

```bash
# Using Docker Compose
cd docker
docker-compose up -d

# Or build manually
docker build -t lute-v3 .
docker run -p 5001:5001 lute-v3
```

### Configuration File

```yaml
# lute/config/config.yml
ENV: prod                    # Environment: prod / dev
DBNAME: lute.db              # Database filename
DATAPATH: /path/to/data      # User data directory (optional, defaults to platform dir)
BACKUP_PATH: /path/to/backups # Backup directory (optional)
```

### Testing

```bash
# Ensure config.yml database name starts with test_

# Run unit tests
inv test

# Run acceptance tests (requires Playwright)
inv accept

# Run all tests
inv fulltest

# Run with coverage
inv coverage --html
```

### CLI Tools

```bash
# Export language terms
flask cli language_export

# Export book terms
flask cli book_term_export

# Batch import books from CSV
flask cli import_books_from_csv
```

### Database Migrations

```bash
# Create new migration script
inv db.newscript --suffix "add_new_feature"

# Export baseline
inv db.export.baseline

# Reset database (test environment only)
inv db.reset
```

---

## Appendix: Key Design Decisions

### 1. Zero-Width Space (ZWS) as Token Separator

Lute uses Unicode zero-width space (`\u200B`) as the separator between tokens. This preserves token boundary information in strings without affecting display. Multi-word term text uses ZWS to join individual tokens.

### 2. Identity Map

`term.model.Repository` maintains an identity map dictionary to avoid loading the same term multiple times in a single request. This is critical for frequent term lookups during reading page rendering.

### 3. Reading Render "Back-to-Front" Algorithm

When calculating overlapping TextItems, the "back-to-front write" strategy is used: longer multi-word terms are processed first, then shorter ones, ensuring the longest text blocks display first.

### 4. Parent-Child Term Sync

Terms can have parent terms (e.g., verb root), and when `sync_status=True`, child and parent statuses are automatically synchronized. This allows users to mark just one conjugated form, and its root form gets the same status.

### 5. Settings Cache

All user settings are cached in the global dict `current_settings`, avoiding a database query on every access. Call `refresh_global_settings()` after settings changes to refresh the cache.

### 6. Bilibili DASH Proxy Architecture

Bilibili videos are played through a three-layer proxy: `stream_info()` fetches the DASH stream metadata from Bilibili's public API → `build_mpd()` generates a local MPD manifest → `proxy_stream()` relays byte-range requests to Bilibili's CDN with proper headers. This completely bypasses the official iframe player's domain whitelist restriction while maintaining full control over the playback UI (scrolling subtitle, loop, auto-pause, transcript panel).