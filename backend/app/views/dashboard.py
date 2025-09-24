# -*- coding: utf-8 -*-
from datetime import datetime, timedelta
from flask import Blueprint, render_template, jsonify, current_app, g, redirect, url_for
from flask_login import login_required, current_user
from sqlalchemy import func, text
from ..models import db, PDTicket, PDProject, PDType, Users

dash_bp = Blueprint("dash", __name__)

# ===== 狀態顯示工具 =====
_STATUS_LABELS = {
    "open": "未受理",
    "received": "已受理",
    "processing": "處理中",
    "replied": "已回覆",
    "assigned": "已指派",
    "onhold": "暫停",
    "closed": "已結案",
}
def _status_label(s: str) -> str:
    k = (s or "").strip().lower()
    return _STATUS_LABELS.get(k, s or "-")

def _status_badge(s: str) -> str:
    """回傳前端用的 pill 色系 class：yellow / green"""
    label = _status_label(s)
    if label in ("未受理", "已受理", "處理中", "暫停"):
        return "yellow"
    if label in ("已回覆", "已指派", "已結案"):
        return "green"
    return "yellow"

# ===== 小工具 =====
def _today():
    now = datetime.utcnow()
    return datetime(now.year, now.month, now.day)

def _last_n_days(n=7):
    end = _today()
    start = end - timedelta(days=n - 1)
    days = [(start + timedelta(days=i)).date() for i in range(n)]
    return start, end, days

# ===== 首頁導向到儀表板 =====
@dash_bp.route("/")
@login_required
def index():
    return redirect(url_for("dash.dashboard"))

# ===== 儀表板主頁 =====
@dash_bp.route("/dashboard")
@login_required
def dashboard():
    log = current_app.logger
    _ = getattr(g, "req_id", "-")

    # 概況
    total_tickets = db.session.query(func.count(PDTicket.ticket_sid)).scalar() or 0
    processing = (
        db.session.query(func.count(PDTicket.ticket_sid))
        .filter(PDTicket.ticket_status.in_(["open", "received"]))
        .scalar()
        or 0
    )
    week_start = _today() - timedelta(days=_today().weekday())  # 本週一
    week_done = (
        db.session.query(func.count(PDTicket.ticket_sid))
        .filter(PDTicket.ticket_status == "closed")
        .filter(PDTicket.update_dt >= week_start)
        .scalar()
        or 0
    )

    # 最近客服單（5 筆）→ 轉中文標籤與樣式
    rows = (
        db.session.query(
            PDTicket.ticket_sid,
            PDTicket.ticket_no,
            PDTicket.ticket_name,
            PDTicket.ticket_status,
            Users.username.label("creator"),
        )
        .join(Users, Users.user_sid == PDTicket.create_usr)
        .order_by(PDTicket.create_dt.desc())
        .limit(5)
        .all()
    )
    recent_tickets = []
    for r in rows:
        status = r.ticket_status or ""
        recent_tickets.append({
            "ticket_sid": r.ticket_sid,
            "ticket_no": r.ticket_no,
            "ticket_name": r.ticket_name,
            "ticket_status": status,
            "status_label": _status_label(status),   # ★ 中文
            "status_badge": _status_badge(status),   # ★ yellow/green
            "creator": r.creator or "-",
        })

    # 進行中專案（附處理中/已回覆統計）
    processing_sub = (
        db.select(func.count(PDTicket.ticket_sid))
        .where(
            PDTicket.project_sid == PDProject.project_sid,
            PDTicket.ticket_status.in_(["open", "received"]),
        )
        .correlate(PDProject)
        .scalar_subquery()
    )
    replied_sub = (
        db.select(func.count(PDTicket.ticket_sid))
        .where(
            PDTicket.project_sid == PDProject.project_sid,
            PDTicket.ticket_status.in_(["replied", "assigned"]),
        )
        .correlate(PDProject)
        .scalar_subquery()
    )
    active_projects = (
        db.session.query(
            PDProject.project_sid,
            PDProject.project_name,
            PDProject.project_status,
            processing_sub.label("processing_cnt"),
            replied_sub.label("replied_cnt"),
        )
        .filter(PDProject.project_status != "已完成")
        .order_by(PDProject.update_dt.desc())
        .limit(3)
        .all()
    )

    # 類型/狀態分布（供初始 SSR；前端圖表仍用 API）
    type_rows = (
        db.session.query(PDType.type_name, func.count(PDTicket.ticket_sid))
        .join(PDTicket, PDTicket.ticket_type == PDType.type_sid)
        .group_by(PDType.type_name)
        .all()
    )
    type_dist = [{"label": n or "未分類", "value": c or 0} for n, c in type_rows]

    status_rows = (
        db.session.query(PDTicket.ticket_status, func.count(PDTicket.ticket_sid))
        .group_by(PDTicket.ticket_status)
        .all()
    )
    status_dist = [{"label": _status_label(s), "value": c or 0} for s, c in status_rows]

    return render_template(
        "dashboard.html",
        total_tickets=total_tickets,
        processing=processing,
        week_done=week_done,
        recent_tickets=recent_tickets,   # ★ 已含中文/樣式
        active_projects=active_projects,
        type_dist=type_dist,
        status_dist=status_dist,
    )

# ====== 圖表資料 API ======

@dash_bp.route("/dashboard/data/ticket_trend7")
@login_required
def data_ticket_trend7():
    start, end, days = _last_n_days(7)
    rows = (
        db.session.query(func.date(PDTicket.create_dt).label("d"), func.count())
        .filter(PDTicket.create_dt >= start)
        .group_by("d")
        .order_by("d")
        .all()
    )
    m = {r[0]: r[1] for r in rows}
    data = [{"date": d.strftime("%m/%d"), "count": int(m.get(d, 0))} for d in days]
    return jsonify(ok=True, data=data)

@dash_bp.route("/dashboard/data/avg_duration7")
@login_required
def data_avg_duration7():
    start, end, days = _last_n_days(7)
    dur = func.timestampdiff(text("HOUR"), PDTicket.create_dt, PDTicket.update_dt)
    rows = (
        db.session.query(func.date(PDTicket.create_dt).label("d"), func.avg(dur))
        .filter(PDTicket.create_dt >= start)
        .filter(PDTicket.ticket_status == "closed")
        .group_by("d")
        .order_by("d")
        .all()
    )
    m = {r[0]: float(r[1]) if r[1] is not None else 0.0 for r in rows}
    data = [{"date": d.strftime("%m/%d"), "hours": round(m.get(d, 0.0), 2)} for d in days]
    return jsonify(ok=True, data=data)

@dash_bp.route("/dashboard/data/type_dist")
@login_required
def data_type_dist():
    rows = (
        db.session.query(PDType.type_name, func.count(PDTicket.ticket_sid))
        .join(PDTicket, PDTicket.ticket_type == PDType.type_sid)
        .group_by(PDType.type_name)
        .all()
    )
    data = [{"label": n or "未分類", "value": c or 0} for n, c in rows]
    return jsonify(ok=True, data=data)

@dash_bp.route("/dashboard/data/status_dist")
@login_required
def data_status_dist():
    rows = (
        db.session.query(PDTicket.ticket_status, func.count(PDTicket.ticket_sid))
        .group_by(PDTicket.ticket_status)
        .all()
    )
    data = [{"label": _status_label(s), "value": c or 0} for s, c in rows]
    return jsonify(ok=True, data=data)
