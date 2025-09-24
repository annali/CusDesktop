from flask import Flask, request, g, jsonify, redirect, url_for, send_from_directory, current_app
from flask_migrate import Migrate
from flask_login import LoginManager, current_user
from uuid import uuid4
import logging
import logging.config
import time
from werkzeug.exceptions import HTTPException
import os

from .models import db, Users
from .config import Config
from .auth import auth_bp
from .views.ticket import ticket_bp
from .views.project import project_bp
from .views.dashboard import dash_bp
from .views.reports_ticket import reports_ticket_bp
from .views.reports_sla import reports_sla_bp
from .views.reports_sla_overdue import reports_sla_overdue_bp
from .views.reports_satisfaction import reports_satisfaction_bp


LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(module)s.%(funcName)s:%(lineno)d "
    "| req=%(req_id)s | %(message)s"
)

# -------------------- LoginManager --------------------
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "請先登入"

@login_manager.user_loader
def load_user(user_id: str):
    try:
        return Users.query.get(int(user_id))
    except Exception:
        return None

# -------------------- 日誌 Filter：補 req_id --------------------
class RequestIdFilter(logging.Filter):
    def filter(self, record):
        try:
            from flask import g as _g
            if not hasattr(record, "req_id"):
                record.req_id = getattr(_g, "req_id", "-")
        except Exception:
            record.req_id = "-"
        return True

def _dict_config(level="INFO"):
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request_id": {"()": RequestIdFilter}},
        "formatters": {"std": {"format": LOG_FORMAT}},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "std",
                "level": level,
                "filters": ["request_id"],
            }
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            "sqlalchemy.engine": {"level": "WARNING"},
            "werkzeug": {"level": "INFO"},
        },
    }

def create_app():
    app = Flask(__name__)

    # ---- logging ----
    logging.config.dictConfig(_dict_config(level="DEBUG"))

    # ---- 設定載入 ----
    app.config.from_object(Config)
    app.config.setdefault("SECRET_KEY", "dev-secret-key-change-me")

    # ---- 初始化擴充 ----
    db.init_app(app)
    Migrate(app, db)
    login_manager.init_app(app)

    # ---- Blueprint ----
    app.register_blueprint(auth_bp)            # /auth/...
    app.register_blueprint(ticket_bp)          # /tickets/...
    app.register_blueprint(project_bp)         # /projects/...
    app.register_blueprint(dash_bp)            # /dashboard
    app.register_blueprint(reports_ticket_bp)  # /reports/...（工單報表）
    app.register_blueprint(reports_sla_bp)     # /reports/...（SLA 報表）
    app.register_blueprint(reports_sla_overdue_bp)     # /reports/...（SLA 逾期報表）
    app.register_blueprint(reports_satisfaction_bp)    # /reports/... (滿意度）

    # ---- 相容轉址：/report/... → /reports/... ----
    @app.route("/report")
    @app.route("/report/")
    @app.route("/report/<path:subpath>")
    def _compat_report_redirect(subpath: str = ""):
        target = "/reports" + ("/" + subpath if subpath else "")
        return redirect(target, code=301)

    # 路由清單（除錯用）
    @app.route("/_routes")
    def _routes():
        lines = []
        for r in app.url_map.iter_rules():
            methods = ",".join(sorted(m for m in r.methods if m not in {"HEAD","OPTIONS"}))
            lines.append(f"{r.rule:35s}  ->  {r.endpoint}  [{methods}]")
        return "<pre>" + "\n".join(sorted(lines)) + "</pre>"

    @app.route('/favicon.ico')
    def favicon():
        static_dir = os.path.join(current_app.root_path, 'static')
        ico_path = os.path.join(static_dir, 'favicon.ico')
        if os.path.exists(ico_path):
            return send_from_directory(static_dir, 'favicon.ico', mimetype='image/vnd.microsoft.icon')
        return ("", 204)

    # ---- 全域例外處理（保留 HTTPException）----
    @app.errorhandler(Exception)
    def _err(e):
        # 對 404/401/403... 等 HTTPException 原樣回傳
        if isinstance(e, HTTPException):
            return e
        current_app.logger.exception("✖ Unhandled exception", extra={"req_id": getattr(g, "req_id", "-")})
        return ("Internal Server Error", 500)

    # ---- 模板全域變數 ----
    @app.context_processor
    def _inject_globals():
        path = request.path if request else "/"
        if path.startswith("/tickets"):
            active = "tickets"
        elif path.startswith("/projects"):
            active = "projects"
        elif path.startswith("/reports"):
            active = "reports"
        else:
            active = "dashboard"
        return {"active_page": active, "current_user": current_user, "MAIN_COLOR": "#527586"}

    # ---- 未授權處理 ----
    @login_manager.unauthorized_handler
    def _unauthorized():
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(ok=False, message="請先登入",
                           next=url_for("auth.login", next=request.path)), 401
        return redirect(url_for("auth.login", next=request.path))

    # ---- 請求前後日誌 ----
    @app.before_request
    def _start_timer():
        g._start_ts = time.time()
        g.req_id = uuid4().hex[:8]
        g.current_user = current_user
        app.logger.info(
            "→ %s %s from=%s ua=%s",
            request.method,
            request.path,
            request.headers.get("X-Forwarded-For", request.remote_addr),
            request.user_agent.string,
            extra={"req_id": g.req_id},
        )

    @app.after_request
    def _after(resp):
        dur = (time.time() - getattr(g, "_start_ts", time.time())) * 1000
        app.logger.info(
            "← %s %s status=%s bytes=%s dur=%.1fms",
            request.method,
            request.path,
            resp.status_code,
            resp.calculate_content_length(),
            dur,
            extra={"req_id": getattr(g, "req_id", "-")},
        )
        return resp

    # ---- 首頁導向 ----
    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("ticket.ticket_list"))
        return redirect(url_for("auth.login"))

    return app
