from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask import current_app
from flask_login import login_user, logout_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from .models import db, Users
from datetime import datetime

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# ---- 讓 Users 相容 Flask-Login（不修改原模型定義）----
if not hasattr(Users, "get_id"):
    Users.get_id = lambda self: str(self.user_sid)
if not hasattr(Users, "is_authenticated"):
    Users.is_authenticated = property(lambda self: True)
if not hasattr(Users, "is_active"):
    Users.is_active = property(lambda self: True)
if not hasattr(Users, "is_anonymous"):
    Users.is_anonymous = property(lambda self: False)

# ---- 註冊 ----
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        account   = (request.form.get("login_account") or "").strip()
        password  = (request.form.get("password") or "").strip()
        password2 = (request.form.get("password2") or "").strip()
        username  = (request.form.get("username") or "").strip()
        email     = (request.form.get("email") or "").strip()

        if not account or not password or not username or not email:
            flash("請完整填寫表單。", "danger")
            return redirect(url_for("auth.register"))

        if password != password2:
            flash("兩次輸入的密碼不一致。", "danger")
            return redirect(url_for("auth.register"))

        # 檢查帳號重複
        if Users.query.filter_by(login_account=account).first():
            flash("此帳號已被註冊。", "danger")
            return redirect(url_for("auth.register"))

        try:
            # ⚠ 關鍵修正：create_usr 先用 None（避免外鍵指到不存在的 user_sid）
            user = Users(
                login_account=account,
                password=generate_password_hash(password),
                username=username,
                email=email,
                dep_sid=0,      # 你的 schema 要求 not null，先給 0（或給一個有效部門 id）
                verify=0,
                status=1,
                create_usr=None,         # ←← 修正點
                create_dt=datetime.utcnow()
            )
            db.session.add(user)
            db.session.commit()

            # 如果你想把 create_usr 設成自己本身，可在拿到主鍵後更新（可選）
            # user.create_usr = user.user_sid
            # db.session.commit()

            flash("註冊成功，請登入。", "success")
            return redirect(url_for("auth.login"))
        except Exception as e:
            current_app.logger.exception("register failed: %s", e)
            db.session.rollback()
            flash("註冊失敗，請稍後再試。", "danger")
            return redirect(url_for("auth.register"))

    # GET
    return render_template("auth_register.html")

# ---- 登入 ----
@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        account   = (request.form.get("login_account") or "").strip()
        password  = (request.form.get("password") or "").strip()
        remember  = True if request.form.get("remember") == "1" else False
        next_url  = request.args.get("next") or request.form.get("next")

        user = Users.query.filter_by(login_account=account).first()
        if not user or not check_password_hash(user.password, password):
            flash("帳號或密碼錯誤。", "danger")
            return redirect(url_for("auth.login"))

        if user.status != 1:
            flash("帳號未啟用。", "warning")
            return redirect(url_for("auth.login"))

        login_user(user, remember=remember)
        user.last_login = datetime.utcnow()
        db.session.commit()

        # 有 next 的話導回原頁，否則到客服單列表
        return redirect(next_url or url_for("ticket.ticket_list"))

    # GET：把可選連結丟給模板，避免模板自己拿 current_app
    return render_template(
        "auth_login.html",
        register_url=url_for("auth.register"),
        forgot_url=None  # 若未實作忘記密碼，就先給 None
    )

# ---- 登出 ----
@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("您已登出。", "info")
    return redirect(url_for("auth.login"))
