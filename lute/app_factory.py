"""
Factory.

Methods: create_app.
"""

import os
import json
import platform
import secrets
import sqlite3
import traceback
import mimetypes
from urllib.parse import quote
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    flash,
    session,
    current_app,
    has_request_context,
    make_response,
    send_from_directory,
    jsonify,
    url_for,
)
from sqlalchemy.event import listens_for
from sqlalchemy.pool import NullPool, Pool

from lute.config.app_config import AppConfig
from lute.db import db
from lute.db.setup.main import setup_db
from lute.db.management import add_default_user_settings
from lute.db.data_cleanup import clean_data
from lute.backup.service import Service as BackupService
from lute.db.demo import Service as DemoService
import lute
import lute.utils.formutils
from lute.utils import static_assets

from lute.parse.registry import init_parser_plugins, supported_parsers
from lute.parse import plugin_installer
from lute.feature.routes import bp as feature_bp
from lute.feature import load_feature_plugins

from lute.models.book import Book
from lute.models.language import Language
from lute.multiuser import context as mu_context
from lute.multiuser import paths as mu_paths
from lute.multiuser import store as mu_store
from lute.multiuser.config_proxy import UserScopedAppConfig
from lute.settings.current import (
    refresh_global_settings,
    current_settings,
    current_hotkeys,
)
from lute.models.repositories import UserSettingRepository
from lute.book.stats import Service as StatsService
from lute.stats.service import get_reading_streak

from lute.ankiexport.routes import bp as anki_bp
from lute.book.routes import bp as book_bp
from lute.bookmarks.routes import bp as bookmarks_bp
from lute.language.routes import bp as language_bp
from lute.multiuser.routes import bp as multiuser_bp
from lute.term.routes import bp as term_bp
from lute.termtag.routes import bp as termtag_bp
from lute.read.routes import bp as read_bp
from lute.bing.routes import bp as bing_bp
from lute.userimage.routes import bp as userimage_bp
from lute.useraudio.routes import bp as useraudio_bp
from lute.termimport.routes import bp as termimport_bp
from lute.backup.routes import bp as backup_bp
from lute.dev_api.routes import bp as dev_api_bp
from lute.settings.routes import bp as settings_bp
from lute.themes.routes import bp as themes_bp
from lute.stats.routes import bp as stats_bp
from lute.cli.commands import bp as cli_bp
from lute.tts.routes import bp as tts_bp


def _setup_app_dir(dirname, readme_content):
    "Create one app directory."
    if not os.path.exists(dirname):
        os.makedirs(dirname)
    readme = os.path.join(dirname, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write(readme_content)


def _setup_app_dirs(app_config):
    """
    App needs the data dir, backups, and other directories.
    """
    dp = app_config.datapath
    required_dirs = [
        [dp, "Lute data folder."],
        [
            app_config.default_user_backup_path,
            "Default path for user backups, can be overridden in settings.",
        ],
        [
            app_config.system_backup_path,
            "Database backups created by Lute at app start, just in case.",
        ],
        [
            app_config.userimagespath,
            "User images.  Each subfolder is a language's ID.",
        ],
        [
            app_config.userthemespath,
            "User themes.  <theme_name>.css files for your personal themes.",
        ],
        [
            app_config.useraudiopath,
            "User audio.  Each file is a book's audio.",
        ],
        [
            app_config.temppath,
            "Temp directory for export file writes, to avoid permissions issues.",
        ],
    ]
    for rec in required_dirs:
        _setup_app_dir(rec[0], rec[1])


def _setup_base_app_dirs(app_config):
    """
    Base-level dirs needed when multi-user mode is on (per-user dirs
    are created from their user config instead).
    """
    dp = app_config.datapath
    required_dirs = [
        [dp, "Lute data folder."],
        [app_config.plugin_datapath, "Data files for plugins."],
        [
            app_config.temppath,
            "Temp directory for export file writes, to avoid permissions issues.",
        ],
    ]
    for rec in required_dirs:
        _setup_app_dir(rec[0], rec[1])


def _add_base_routes(app, app_config):
    """
    Add some basic routes.
    """

    @app.context_processor
    def inject_menu_bar_vars():
        """
        Inject backup settings into the all templates for the menu bar.
        """
        # Pre-login requests (multi-user mode) have no user scope: no
        # db access is possible, so serve neutral values.  The login
        # page is standalone and doesn't use these.
        if mu_store.enabled() and not mu_context.get_current_user():
            return {
                "have_languages": False,
                "backup_enabled": False,
                "backup_directory": "",
                "backup_last_display_date": None,
                "backup_time_since": None,
                "user_settings": json.dumps({}),
                "user_hotkeys": json.dumps({}),
                "current_theme": "Default.css",
                "lute_version": lute.__version__,
                "asset_cache_bust": lute.ASSET_CACHE_BUST,
                "multiuser_enabled": True,
                "current_username": None,
                "is_admin": False,
            }
        us_repo = UserSettingRepository(db.session)
        bs = us_repo.get_backup_settings()
        have_languages = len(db.session.query(Language).all()) > 0
        # Templates can be rendered outside a request (e.g. background
        # rendering in tests); session is only readable in requests.
        req_username = session.get("user") if has_request_context() else None
        ret = {
            "have_languages": have_languages,
            "backup_enabled": bs.backup_enabled,
            "backup_directory": bs.backup_dir,
            "backup_last_display_date": bs.last_backup_display_date,
            "backup_time_since": bs.time_since_last_backup,
            "user_settings": json.dumps(current_settings()),
            "user_hotkeys": json.dumps(current_hotkeys()),
            "current_theme": us_repo.get_value("current_theme"),
            "lute_version": lute.__version__,
            "asset_cache_bust": lute.ASSET_CACHE_BUST,
            "multiuser_enabled": mu_store.enabled(),
            "current_username": req_username,
            "is_admin": mu_store.enabled() and mu_store.is_admin(req_username),
        }
        return ret

    @app.route("/")
    def index():
        demosvc = DemoService(db.session)
        is_production = not demosvc.contains_demo_data()
        us_repo = UserSettingRepository(db.session)
        bkp_settings = us_repo.get_backup_settings()

        have_books = len(db.session.query(Book).all()) > 0
        have_languages = len(db.session.query(Language).all()) > 0
        language_choices = lute.utils.formutils.language_choices(
            db.session, "(all languages)"
        )
        current_language_id = lute.utils.formutils.valid_current_language_id(db.session)

        bs = BackupService(db.session)
        should_run_auto_backup = bs.should_run_auto_backup(bkp_settings)
        # Only back up if we have books, otherwise the backup is
        # kicked off when the user empties the demo database.
        if is_production and have_books and should_run_auto_backup:
            return redirect("/backup/backup", 302)

        warning_msg = bs.backup_warning(bkp_settings)
        backup_show_warning = (
            bkp_settings.backup_warn
            and bkp_settings.backup_enabled
            and warning_msg != ""
        )

        demosvc = DemoService(db.session)
        response = make_response(
            render_template(
                "index.html",
                hide_homelink=True,
                dbname=app_config.dbname,
                datapath=app_config.datapath,
                tutorial_book_id=demosvc.tutorial_book_id(),
                have_books=have_books,
                have_languages=have_languages,
                language_choices=language_choices,
                current_language_id=current_language_id,
                is_production_data=is_production,
                backup_show_warning=backup_show_warning,
                backup_warning_msg=warning_msg,
                reading_streak=get_reading_streak(db.session),
                show_streak_on_home=current_settings().get(
                    "show_streak_on_home", False
                ),
            )
        )
        return response

    @app.route("/refresh_all_stats", methods=["GET", "POST"])
    def refresh_all_stats():
        books_to_update = db.session.query(Book).filter(Book.archived == 0).all()
        svc = StatsService(db.session)
        for book in books_to_update:
            svc.mark_stale(book)
        # When called via AJAX the frontend refreshes the stats columns in
        # place (no redirect), so return JSON instead of navigating away.
        if request.method == "POST":
            return jsonify({"ok": True})
        return redirect("/", 302)

    @app.route("/wipe_database")
    def wipe_db():
        if mu_store.enabled() and not mu_store.is_admin(session.get("user")):
            flash("Only an admin can wipe the database.")
            return redirect("/", 302)
        demosvc = DemoService(db.session)
        if demosvc.contains_demo_data():
            demosvc.delete_demo_data()
            msg = """
            The database has been wiped clean.  Have fun! <br /><br />
            <i>(Lute has automatically enabled backups --
            change your <a href="/settings/index">Settings</a> as needed.)</i>
            """
            flash(msg)
        return redirect("/", 302)

    @app.route("/remove_demo_flag")
    def remove_demo():
        demosvc = DemoService(db.session)
        if demosvc.contains_demo_data():
            demosvc.remove_flag()
            msg = """
            Demo mode deactivated. Have fun! <br /><br />
            <i>(Lute has automatically enabled backups --
            change your <a href="/settings/index">Settings</a> as needed.)</i>
                        """
            flash(msg)
        return redirect("/", 302)

    @app.route("/version")
    def show_version():
        ac = current_app.env_config
        return render_template(
            "version.html",
            version=lute.__version__,
            datapath=ac.datapath,
            database=ac.dbfilename,
            is_docker=ac.is_docker,
        )

    @app.route("/info")
    def show_info():
        """
        Json return of some data.

        Used in lute.verify module for tests.

        This likely belongs in a different 'api' location,
        but leaving it here for now.
        """
        ret = {
            "version": lute.__version__,
            "datapath": current_app.env_config.datapath,
            "database": current_app.env_config.dbfilename,
        }
        return jsonify(ret)

    @app.route("/static/js/never_cache/<path:filename>")
    def custom_js(filename):
        """
        Serve JS files with long-term cache headers.
        Cache busting is handled by the ?v= query parameter.
        """
        response = make_response(send_from_directory("static/js", filename))
        # Versioned URLs (?v=...) handle cache busting, so we can
        # safely cache these files for a long time.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.route("/sw.js")
    def service_worker():
        "Serve the PWA service worker from root scope."
        response = make_response(send_from_directory("static", "sw.js"))
        response.headers["Content-Type"] = "application/javascript; charset=utf-8"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.route("/manifest.webmanifest")
    @app.route("/manifest.json")
    def pwa_manifest():
        "Serve the PWA web manifest with the correct MIME type."
        response = make_response(send_from_directory("static", "manifest.json"))
        response.headers["Content-Type"] = "application/manifest+json; charset=utf-8"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return response

    @app.errorhandler(500)
    def _internal_server_error(e):  # pylint: disable=unused-argument
        """
        Custom error handler for 500 Internal Server Error
        """
        exception_info = traceback.format_exc()
        # Should add logging ...
        # app.logger.error(exception_info)
        return (
            render_template(
                "errors/500_error.html",
                exception_info=exception_info,
                version=lute.__version__,
                platform=platform.platform(),
                is_docker=current_app.env_config.is_docker,
            ),
            500,
        )

    @app.errorhandler(404)
    def _page_not_found(e):  # pylint: disable=unused-argument
        "Show custom error page on 404."
        return (
            render_template(
                "errors/404_error.html",
                version=lute.__version__,
                requested_url=request.url,
                referring_page=request.referrer,
            ),
            404,
        )


def _load_or_create_secret_key(app_config):
    """
    Load the app SECRET_KEY, creating a random one on first use.

    A real secret is required for signed sessions in multi-user mode;
    it's persisted in the datapath so sessions survive restarts.
    """
    keyfile = os.path.join(app_config.datapath, ".secret_key")
    if os.path.exists(keyfile):
        with open(keyfile, "r", encoding="utf-8") as f:
            existing = f.read().strip()
            if existing:
                return existing
    new_key = secrets.token_hex(32)
    fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new_key)
    return new_key


def _create_app(app_config, extra_config):
    """
    Create the app using the given configuration,
    and init the SqlAlchemy db.
    """

    app = Flask(__name__, instance_path=app_config.datapath)

    def _connect_current_db():
        """
        Open a raw connection to the CURRENT scope's database.

        Multi-user mode: each user gets their own sqlite file, resolved
        per connection checkout from the request's user scope.  A db
        touch without a user scope is a bug -- failing loudly here
        beats silently creating an empty base db file.
        Single-user mode: this is the base db file, as before.
        """
        if mu_store.enabled():
            username = mu_context.get_current_user()
            if username:
                return sqlite3.connect(mu_paths.user_dbfilename(app_config, username))
            raise RuntimeError(
                "Multi-user mode is on, but no user scope is set for this "
                "database access.  Boot-time code must run inside "
                "multiuser.context.user_scope(username)."
            )
        return sqlite3.connect(app_config.dbfilename)

    config = {
        "SECRET_KEY": _load_or_create_secret_key(app_config),
        "DATABASE": app_config.dbfilename,
        "ENV": app_config.env,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{app_config.dbfilename}",
        # The URI above only selects the sqlite dialect; connections
        # are opened per checkout by _connect_current_db, so no pooled
        # connection can ever point at another user's db file.
        "SQLALCHEMY_ENGINE_OPTIONS": {
            "creator": _connect_current_db,
            "poolclass": NullPool,
        },
        "DATAPATH": app_config.datapath,
        # ref https://flask-sqlalchemy.palletsprojects.com/en/2.x/config/
        # Don't track mods.
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        # Disable CSRF -- this is a local app, and it's highly
        # unlikely that a malicious site will try to hack anyone's Lute data.
        # ref https://stackoverflow.com/questions/5207160/
        #   what-is-a-csrf-token-what-is-its-importance-and-how-does-it-work
        "WTF_CSRF_ENABLED": False,
        # Allow large uploads (MP3 audio + subtitle files for youtube/mp3
        # book imports).  200 MB matches the Nginx client_max_body_size
        # on the production server.
        "MAX_CONTENT_LENGTH": 200 * 1024 * 1024,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": 86400 * 30,
    }
    if app_config.env == "prod":
        config["SESSION_COOKIE_SECURE"] = True

    final_config = {**config, **extra_config}
    app.config.from_mapping(final_config)

    # Attach the app_config to app so it's available at runtime.
    # Wrapped in a proxy: user-scoped path attributes resolve to the
    # logged-in user's directories when multi-user mode is on.
    app.env_config = UserScopedAppConfig(app_config)

    # Force template auto-reload so that template changes are picked up
    # without needing to restart the server (especially in prod env).
    app.jinja_env.auto_reload = True

    # vstatic(): url_for('static', ...) plus a content-hash ?v= param, so
    # vendored assets (served immutable for a year) are re-fetched exactly
    # when their content changes.  ref lute/utils/static_assets.py
    app.jinja_env.globals["vstatic"] = static_assets.make_vstatic(
        app.static_folder,
        lambda filename: url_for("static", filename=filename),
    )

    db.init_app(app)

    @listens_for(Pool, "connect")
    def _pragmas_on_connect(dbapi_con, con_record):  # pylint: disable=unused-argument
        dbapi_con.execute("pragma recursive_triggers = on;")
        dbapi_con.execute("pragma foreign_keys = on;")

    with app.app_context():
        if mu_store.enabled():
            # Multi-user mode: create/verify schema and seed default
            # settings in EACH user's own database.
            for username in [u["username"] for u in mu_store.users()]:
                with mu_context.user_scope(username):
                    db.create_all()
                    add_default_user_settings(
                        db.session,
                        current_app.env_config.default_user_backup_path,
                    )
                    refresh_global_settings(db.session)
        else:
            db.create_all()
            add_default_user_settings(db.session, app_config.default_user_backup_path)
            refresh_global_settings(db.session)
    app.db = db

    _add_base_routes(app, app_config)

    # The before_request hook checks a single boolean flag.
    # The import is at module level so it only happens once.
    from lute.backup.service import Service as _BackupServiceClass

    @app.before_request
    def _redirect_credentialed_url():
        """
        Redirect BasicAuth page loads to a clean URL.

        When the site is accessed via a credentialed URL
        (https://user:pass@host/...), browsers block all sub-resource
        requests that carry credentials in the URL (CSS, JS, images).
        The server-side redirect below ensures the browser navigates to
        the clean URL before any HTML is rendered, so all sub-resource
        requests use the clean origin.

        We detect BasicAuth by the Authorization header (Nginx strips
        URL credentials before forwarding to Flask).  A cookie prevents
        redirect loops on subsequent requests.
        """
        # Only redirect GET navigation requests
        if request.method != "GET":
            return

        # Only redirect when BasicAuth is used
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return

        # Skip if already redirected (cookie present)
        if request.cookies.get("lute_authed"):
            return

        # Only redirect page loads (HTML), not AJAX or static resources
        accept = request.headers.get("Accept", "")
        if "text/html" not in accept:
            return

        # Skip AJAX requests
        if request.headers.get("X-Requested-With"):
            return

        # Skip static files
        if request.path.startswith("/static/"):
            return

        # Build the clean URL (no credentials)
        _xfp = request.headers.get("X-Forwarded-Proto", "")
        _proto = (
            "https"
            if (app.config.get("ENV") == "prod" or _xfp == "https")
            else request.scheme
        )
        clean_url = f"{_proto}://{request.host}{request.full_path}"
        # full_path includes trailing '?' even without query string
        if clean_url.endswith("?"):
            clean_url = clean_url[:-1]

        response = redirect(clean_url, 302)
        response.set_cookie(
            "lute_authed",
            "1",
            httponly=True,
            secure=True,
            samesite="Lax",
            max_age=86400 * 30,  # 30 days
        )
        return response

    @app.before_request
    def _multiuser_auth_gate():
        """
        Route each request to its user's scope.

        Multi-user mode: unauthenticated requests are redirected to
        /login (static assets excepted); authenticated requests run
        against the logged-in user's own database and data paths.
        Single-user mode: no-op -- the scope stays on the default
        (base) database and paths, exactly as before.
        """
        if not mu_store.enabled():
            mu_context.set_current_user(None)
            return

        path = request.path
        if path == "/login" or path.startswith("/static/") or path == "/favicon.ico":
            mu_context.set_current_user(None)
            return

        username = session.get("user")
        if username and mu_store.get_user(username) is not None:
            mu_context.set_current_user(username)
            return

        mu_context.set_current_user(None)
        session.clear()
        login_url = url_for("multiuser.login")
        if request.method == "GET":
            login_url += "?next=" + quote(request.path)
        return redirect(login_url)

    @app.before_request
    def _before_request():
        """
        Reset engine and refresh settings after a backup restore.
        Only runs once (after restore_backup sets the flag).
        """
        if not _BackupServiceClass._engine_needs_reset:
            return
        _BackupServiceClass._engine_needs_reset = False
        try:
            db.session.remove()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        # Run any pending schema migrations on the restored database.
        # A backup restored from an older schema (e.g. made by upstream
        # Lute) may be missing Song-specific columns (LgKiwi*, manga/
        # pdf/srt book fields, etc.).  setup_db no-ops quickly if the
        # schema is already current.
        try:
            setup_db(current_app.env_config)
        except Exception:  # pylint: disable=broad-exception-caught
            current_app.logger.error(
                "Running migrations after restore failed.", exc_info=True
            )
        try:
            db.engine.dispose()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        try:
            db.engine.connect().close()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        try:
            refresh_global_settings(db.session)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        try:
            from lute.parse.mecab_parser import JapaneseParser

            JapaneseParser._is_supported = None
            JapaneseParser._old_mecab_path = None
            JapaneseParser._invalidate_mecab_cache()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    @app.after_request
    def _no_cache_dynamic_pages(response):
        """
        Prevent caching of dynamic HTML pages.

        Every Lute page embeds per-user state (flash messages, term
        statuses, language settings, rendered text).  If Cloudflare or
        the browser caches the HTML, CDNs serve a stale copy after a
        change -- e.g. toggling a language Active updates the DB but a
        cached /language/index still shows "Frozen".  So all text/html
        page responses are marked no-store.  Static assets (CSS/JS/
        images), which are not text/html, keep their long cache headers.
        """
        if request.method == "GET" and response.mimetype == "text/html":
            response.headers[
                "Cache-Control"
            ] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    app.register_blueprint(language_bp)
    app.register_blueprint(anki_bp)
    app.register_blueprint(book_bp)
    app.register_blueprint(bookmarks_bp)
    app.register_blueprint(term_bp)
    app.register_blueprint(termtag_bp)
    app.register_blueprint(read_bp)
    app.register_blueprint(bing_bp)
    app.register_blueprint(userimage_bp)
    app.register_blueprint(useraudio_bp)
    app.register_blueprint(termimport_bp)
    app.register_blueprint(backup_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(themes_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(cli_bp)
    app.register_blueprint(tts_bp)
    app.register_blueprint(multiuser_bp)
    if app_config.is_test_db:
        app.register_blueprint(dev_api_bp)

    # Feature plugin support.
    # - feature_bp always serves empty fragments unless plugins add items.
    # - load_feature_plugins discovers installed plugins and wires up any
    #   blueprints / menu items / settings tiles they register.
    app.register_blueprint(feature_bp)
    load_feature_plugins(app)

    return app


def _init_parser_plugins(app, plugin_data_path, outfunc):
    "Load and init plugins."
    outfunc("Initializing parsers from plugins ...")
    init_parser_plugins()

    # Auto-install missing whitelisted parser plugins for languages that
    # already exist in the DB (e.g. Korean created before its parser was
    # pluginized).  These never hit the install-on-predefined-load path,
    # so without this step users would be stuck with an unusable parser.
    outfunc("Checking parser plugins for existing languages ...")
    # Multi-user mode: every user's db has its own languages, so run
    # the check per user.  Single-user mode: one pass on the base db.
    if mu_store.enabled():
        scopes = [u["username"] for u in mu_store.users()]
    else:
        scopes = [None]
    for username in scopes:
        with mu_context.user_scope(username), app.app_context():
            for name, ok, message in plugin_installer.ensure_existing_language_parsers(
                db.session
            ):
                status = "OK" if ok else "FAILED"
                outfunc(f"  * {name}: {status} - {message}")

    parsers = supported_parsers()
    parsers_with_extra_data = [
        (typename, klass) for typename, klass in parsers if klass.uses_data_directory()
    ]
    if len(parsers_with_extra_data) > 0:
        # outfunc("Creating data folders for plugins ...")
        _setup_app_dir(plugin_data_path, "Data files for plugins.")
    for pair in parsers_with_extra_data:
        typename, klass = pair
        dirname = os.path.join(plugin_data_path, typename)
        klass.data_directory = dirname

        readme_content = f"Extra data for {klass.name()} plugin."
        _setup_app_dir(dirname, readme_content)
        klass.init_data_directory()
        # outfunc(f"  * {klass.name()}: {dirname}")

    outfunc("Enabled parsers:")
    for _, v in supported_parsers():
        outfunc(f"  * {v.name()}")


mimetypes.add_type("text/css", ".css")
# Audio books store uploaded files by their original extension and stream
# them via /useraudio/stream/<book_id>.  Register the audio types the
# upload form allows (see book.forms.ALLOWED_AUDIO_EXTENSIONS) so
# browsers play them instead of falling back to application/octet-stream.
mimetypes.add_type("audio/mpeg", ".mp3")
mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("audio/mp4", ".m4b")
mimetypes.add_type("audio/x-wav", ".wav")
mimetypes.add_type("audio/ogg", ".ogg")
mimetypes.add_type("audio/ogg", ".opus")
mimetypes.add_type("audio/aac", ".aac")
mimetypes.add_type("audio/flac", ".flac")
mimetypes.add_type("audio/webm", ".webm")


def create_app(
    app_config_path=None,
    extra_config=None,
    output_func=None,
):
    """
    App factory.  Calls dbsetup, and returns Flask app.

    Args:
    - app_config_path: path to yml file.  If None, use root config or default.
    - extra_config: dict, e.g. pass { 'TESTING': True } during unit tests.
    """

    def null_print(s):  # pylint: disable=unused-argument
        pass

    outfunc = output_func or null_print

    if app_config_path is None:
        if os.path.exists("config.yml"):
            app_config_path = "config.yml"
        else:
            app_config_path = AppConfig.default_config_filename()

    app_config = AppConfig(app_config_path)
    mu_store.load(app_config)
    if mu_store.enabled():
        # Multi-user mode: every user gets their own db and data dirs,
        # migrated/created and schema-migrated individually.
        _setup_base_app_dirs(app_config)
        for userinfo in mu_store.users():
            ucfg = mu_paths.user_config(app_config, userinfo["username"])
            mu_paths.ensure_user_dirs(ucfg)
            setup_db(ucfg, output_func)
    else:
        _setup_app_dirs(app_config)
        setup_db(app_config, output_func)

    if extra_config is None:
        extra_config = {}
    outfunc("Initializing app.")
    app = _create_app(app_config, extra_config)

    # Plugins are loaded after the app, as they may use settings etc.
    _init_parser_plugins(app, app_config.plugin_datapath, outfunc)

    return app


def data_initialization(session, output_func=None):
    """
    Any extra data setup.

    TODO: rework data initialization.  The DB setup can be handled
    outside of the application context, as IMO it's clearer to manage
    the data separately from the thing that uses the data.  This
    requires moving from flask-sqlalchemy to plain sqlalchemy.
    """

    def _null_print(s):  # pylint: disable=unused-argument
        pass

    outfunc = output_func or _null_print

    demosvc = DemoService(session)
    if demosvc.should_load_demo_data():
        outfunc("Loading demo data.")
        demosvc.load_demo_data()

    # TODO valid parsers: do parser check, mark valid as active, invalid as inactive.

    clean_data(session, outfunc)
