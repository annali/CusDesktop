# -*- coding: utf-8 -*-
from datetime import datetime
from collections import defaultdict
import os, uuid

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, send_file
from flask_login import login_required, current_user
from sqlalchemy import func, or_
from sqlalchemy.orm import aliased

from ..models import (
    db, PDProject, Users, PDTicket, PDComment, PDCase, PDAttachment, PDActivityLog
)

project_bp = Blueprint("project", __name__, url_prefix="/projects")

# =========================
# 共用：樣式 / 工具
# =========================

def _status_badge(s: str) -> str:
    mapping = {
        "規劃中": "badge bg-secondary",
        "進行中": "badge bg-warning text-dark",
        "已完成": "badge bg-success",
    }
    return mapping.get(s or "", "badge bg-light text-dark border")


# ---- 客服單狀態對應：英文/舊值 → 中文顯示 -----------------------------------------
# 盡量把常見拼法都涵蓋，最後都回傳中文顯示文字
_TICKET_DISPLAY = {
    # 進行中類
    "open": "未受理",
    "in_progress": "處理中",
    "in progress": "處理中",
    "processing": "處理中",
    "處理中": "處理中",

    # 已收件 / 暫停
    "received": "已受理",
    "已接收": "已受理",
    "onhold": "暫停處理",
    "on_hold": "暫停處理",
    "on hold": "暫停處理",
    "暫停處理": "暫停處理",

    # 已回覆
    "replied": "已回覆",
    "已回覆": "已回覆",

    # 已結案
    "closed": "已結案",
    "已結案": "已結案",

    # 待處理
    "pending": "待處理",
    "待處理": "待處理",
}

def _ticket_status_display(s: str) -> str:
    raw = (s or "").strip()
    if not raw:
        return "—"
    key = raw.lower().replace("-", "_").strip()
    return _TICKET_DISPLAY.get(key, _TICKET_DISPLAY.get(raw, raw))

def _ticket_status_badge(s: str) -> str:
    disp = _ticket_status_display(s)
    # 視覺可依主題色調整
    if disp in ("未受理", "已受理"):
        return "badge bg-secondary"
    if disp in ("處理中",):
        return "badge bg-warning text-dark"
    if disp in ("暫停處理",):
        return "badge bg-dark-subtle text-dark"
    if disp in ("已回覆",):
        return "badge bg-info"
    if disp in ("已結案",):
        return "badge bg-success"
    return "badge bg-light text-dark border"


def _human_size(num_bytes: int) -> str:
    n = float(num_bytes)
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if n < 1024.0:
            return (f"{int(n)} {unit}" if unit == 'B' else f"{n:.2f} {unit}")
        n /= 1024.0
    return f"{n:.2f} PB"

def _upload_root() -> str:
    # 可用 app.config['UPLOAD_DIR'] 覆蓋，預設專案根 uploads 目錄
    root = current_app.config.get("UPLOAD_DIR") or os.path.join(current_app.root_path, "..", "..", "uploads")
    root = os.path.abspath(root)
    os.makedirs(root, exist_ok=True)
    return root

def _next_activity_id() -> int:
    return (db.session.query(func.coalesce(func.max(PDActivityLog.activity_log_sid), 0)).scalar() or 0) + 1

def _add_log(*, ref_type: str, ref_sid: int, action: str, field_name: str,
             old_value, new_value, message: str, actor_id: int | None):
    row = PDActivityLog(
        activity_log_sid=_next_activity_id(),
        ref_type=ref_type,
        ref_sid=ref_sid,
        action=action,
        field_name=field_name,
        old_value=str(old_value) if old_value is not None else None,
        new_value=str(new_value) if new_value is not None else None,
        message=message,
        status=1,
        create_usr=actor_id,
        create_dt=datetime.utcnow(),
    )
    db.session.add(row)

# =========================
# 專案列表
# =========================

@project_bp.route("/", methods=["GET"])
@login_required
def project_list():
    q = request.args.get("q", "", type=str).strip()
    status_param = request.args.get("status", "", type=str).strip()

    status_map = {
        "planning": "規劃中",
        "progress": "進行中",
        "done": "已完成",
        "規劃中": "規劃中",
        "進行中": "進行中",
        "已完成": "已完成",
        "": "",
    }
    status_val = status_map.get(status_param, "")

    query = (
        db.session.query(PDProject, Users.username.label("manager_name"))
        .join(Users, Users.user_sid == PDProject.project_manager_usr)
    )

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                PDProject.project_no.ilike(like),
                PDProject.project_name.ilike(like),
                PDProject.project_description.ilike(like),
            )
        )

    if status_val:
        query = query.filter(PDProject.project_status == status_val)

    rows = query.order_by(PDProject.project_no.asc()).all()

    projects = []
    for p, manager_name in rows:
        projects.append(
            {
                "id": p.project_sid,
                "code": p.project_no,
                "name": p.project_name,
                "status": p.project_status,
                "status_badge": _status_badge(p.project_status),
                "manager": manager_name,
                "end_dt": p.end_dt,
            }
        )

    return render_template(
        "projects.html",
        q=q,
        projects=projects,
    )

# =========================
# 專案明細（含回覆/客服單/附件）
# =========================

@project_bp.route("/<int:project_sid>", methods=["GET"])
@login_required
def project_detail(project_sid: int):
    p: PDProject = PDProject.query.get_or_404(project_sid)
    manager = Users.query.get(p.project_manager_usr)
    manager_name = manager.username if manager else "-"

    # 讓前端能指定預設分頁（若未指定，仍可用原本預設）
    active_tab = (request.args.get("tab") or "replies").strip()

    # ---- 客服單狀態統計（把英文也算進相對應中文） -------------------------------
    agg = (
        db.session.query(PDTicket.ticket_status, func.count(PDTicket.ticket_sid))
        .filter(PDTicket.project_sid == project_sid)
        .group_by(PDTicket.ticket_status)
        .all()
    )
    counts = defaultdict(int)
    for s, c in agg:
        disp = _ticket_status_display(s or "")
        counts[disp] += c

    # 你的右側小卡片只顯示「處理中、已回覆」，這裡把「已收件、暫停處理」也歸入處理中群組
    processing = (
        counts.get("處理中", 0)
        + counts.get("已受理", 0)
        + counts.get("暫停處理", 0)
    )
    replied = counts.get("已回覆", 0)
    total = sum(counts.values())

    # ====== 客服單：搜尋 + 排序（比照「相關案件」分頁邏輯） ======
    ticket_q = (request.args.get("ticket_q") or "").strip()
    ticket_sort = (request.args.get("ticket_sort") or "code").strip()
    ticket_dir = (request.args.get("ticket_dir") or "asc").lower()

    # 取每張客服單對應最新案件的負責人（若有）
    case_max_subq = (
        db.session.query(
            PDCase.ticket_sid.label("t_sid"),
            func.max(PDCase.case_sid).label("max_case_sid"),
        )
        .group_by(PDCase.ticket_sid)
        .subquery()
    )
    Assignee = aliased(Users)

    t_query = (
        db.session.query(
            PDTicket.ticket_sid,
            PDTicket.ticket_no,
            PDTicket.ticket_name,
            PDTicket.ticket_status,
            Users.username.label("creator"),
            Assignee.username.label("assignee"),
            PDTicket.create_dt,
            PDTicket.update_dt,
            PDTicket.ticket_description,
        )
        .join(Users, Users.user_sid == PDTicket.create_usr, isouter=True)
        .outerjoin(case_max_subq, case_max_subq.c.t_sid == PDTicket.ticket_sid)
        .outerjoin(PDCase, PDCase.case_sid == case_max_subq.c.max_case_sid)
        .outerjoin(Assignee, Assignee.user_sid == PDCase.case_assignee_usr)
        .filter(PDTicket.project_sid == project_sid)
    )

    if ticket_q:
        like = f"%{ticket_q}%"
        t_query = t_query.filter(PDTicket.ticket_name.ilike(like))

    sort_map = {
        "code": PDTicket.ticket_no,
        "name": PDTicket.ticket_name,
        "assignee": Assignee.username,
        "created_at": PDTicket.create_dt,
        "status": PDTicket.ticket_status,
    }
    sort_expr = sort_map.get(ticket_sort, PDTicket.ticket_no)
    t_query = t_query.order_by(
        sort_expr.desc() if ticket_dir == "desc" else sort_expr.asc(),
        PDTicket.ticket_sid.desc()
    )

    ticket_rows = t_query.all()
    tickets = []
    for r in ticket_rows:
        tickets.append(
            {
                "id": r.ticket_sid,
                "code": r.ticket_no,
                "name": r.ticket_name,
                "status": r.ticket_status or "",
                "status_display": _ticket_status_display(r.ticket_status or ""),
                "status_badge": _ticket_status_badge(r.ticket_status or ""),
                "creator": r.creator or "-",
                "assignee": r.assignee or "-",
                "created_at": r.create_dt,
                "updated_at": r.update_dt,
                "desc": r.ticket_description or "",
            }
        )

    # 產生表頭排序連結（固定停留在 tickets 分頁）
    def _build_ticket_sort_url(key: str) -> str:
        next_dir = "desc" if (ticket_sort == key and ticket_dir == "asc") else "asc"
        return url_for(
            "project.project_detail",
            project_sid=project_sid,
            tab="tickets",
            ticket_q=ticket_q,
            ticket_sort=key,
            ticket_dir=next_dir,
            _anchor="pane-tickets",
        )

    ticket_sort_urls = {k: _build_ticket_sort_url(k) for k in sort_map.keys()}

    # 專案留言
    cmts = (
        db.session.query(
            PDComment.comment_sid,
            PDComment.content,
            PDComment.create_dt,
            Users.username.label("author"),
        )
        .join(Users, Users.user_sid == PDComment.author_sid, isouter=True)
        .filter(PDComment.ref_type == "project", PDComment.ref_sid == project_sid, PDComment.status == 1)
        .order_by(PDComment.create_dt.desc())
        .all()
    )
    comments = [
        {
            "id": c.comment_sid,
            "author": c.author or "-",
            "content": c.content,
            "ts": c.create_dt,
        }
        for c in cmts
    ]

    # 附件（只取啟用中的附件 status=1）
    attach_rows = (
        PDAttachment.query
        .filter_by(ref_type="project", ref_sid=project_sid, status=1)
        .order_by(PDAttachment.create_dt.desc())
        .all()
    )
    attachments = [
        {
            "id": a.attachmen_sid,      # 注意模型主鍵拼字
            "name": a.attachment_name,
            "size": a.attachment_size,
            "code": a.attachment_code,
            "created_at": a.create_dt,
        }
        for a in attach_rows
    ]

    meta = {
        "code": p.project_no,
        "created_at": p.create_dt.strftime("%Y/%m/%d") if p.create_dt else "-",
    }

    return render_template(
        "project_detail.html",
        active_page="projects",
        p=p,
        manager_name=manager_name,
        processing=processing,
        replied=replied,
        total=total,
        comments=comments,
        tickets=tickets,
        attachments=attachments,
        status_badge=_status_badge(p.project_status),
        meta=meta,

        # 分頁/搜尋/排序 參數（客服單）
        active_tab=active_tab,
        ticket_q=ticket_q,
        ticket_sort=ticket_sort,
        ticket_dir=ticket_dir,
        ticket_sort_urls=ticket_sort_urls,
    )

# =========================
# 專案留言
# =========================

@project_bp.route("/<int:project_sid>/comments", methods=["POST"])
@login_required
def project_comment_add(project_sid: int):
    PDProject.query.get_or_404(project_sid)
    content = (request.form.get("content") or "").strip()
    if not content:
        flash("請輸入回覆內容", "warning")
        return redirect(url_for("project.project_detail", project_sid=project_sid, tab="replies"))

    c = PDComment(
        ref_type="project",
        ref_sid=project_sid,
        author_sid=current_user.user_sid,
        content=content,
        status=1,
        create_usr=current_user.user_sid,
        create_dt=datetime.utcnow(),
    )
    db.session.add(c)
    db.session.commit()
    return redirect(url_for("project.project_detail", project_sid=project_sid, tab="replies", _anchor="pane-replies"))

# =========================
# 附件：上傳（含寫入歷程）
# =========================

@project_bp.route("/<int:project_sid>/attachments", methods=["POST"])
@login_required
def project_upload_attachment(project_sid: int):
    PDProject.query.get_or_404(project_sid)

    f = request.files.get("file")
    if not f or not f.filename.strip():
        flash("請選擇要上傳的檔案", "warning")
        return redirect(url_for("project.project_detail", project_sid=project_sid, tab="files", _anchor="pane-files"))

    # 寫檔
    root = _upload_root()
    rel_dir = os.path.join("projects", str(project_sid))
    abs_dir = os.path.join(root, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    ext = os.path.splitext(f.filename)[1]
    safe_name = f"{uuid.uuid4().hex}{ext}"
    abs_path = os.path.join(abs_dir, safe_name)
    f.save(abs_path)

    size = os.path.getsize(abs_path)

    a = PDAttachment(
        ref_type="project",
        ref_sid=project_sid,
        attachment_code=os.path.join(rel_dir, safe_name).replace("\\", "/"),
        attachment_name=f.filename,
        attachment_size=_human_size(size),
        status=1,
        create_usr=getattr(current_user, "user_sid", None),
        create_dt=datetime.utcnow(),
    )
    db.session.add(a)
    db.session.flush()  # 先拿到 PK

    # 歷程
    actor_name = getattr(current_user, "username", "系統")
    _add_log(
        ref_type="project",
        ref_sid=project_sid,
        action="CREATE",
        field_name="attachment",
        old_value=None,
        new_value=a.attachment_name,
        message=f"使用者「{actor_name}」上傳附件：{a.attachment_name}（{a.attachment_size}）",
        actor_id=getattr(current_user, "user_sid", None),
    )

    db.session.commit()
    flash("檔案已上傳", "success")
    return redirect(url_for("project.project_detail", project_sid=project_sid, tab="files", _anchor="pane-files"))

# =========================
# 附件：下載
# =========================

@project_bp.route("/attachments/<int:attach_id>/download", methods=["GET"])
@login_required
def project_download_attachment(attach_id: int):
    a = db.session.get(PDAttachment, attach_id)
    if not a or a.status != 1 or a.ref_type != "project":
        return ("Not Found", 404)

    root = _upload_root()
    abs_path = os.path.join(root, a.attachment_code)
    if not os.path.exists(abs_path):
        return ("Not Found", 404)

    return send_file(abs_path, as_attachment=True, download_name=a.attachment_name)

# =========================
# 附件：刪除（含寫入歷程）
# =========================

@project_bp.route("/attachments/<int:attach_id>/delete", methods=["POST"])
@login_required
def project_delete_attachment(attach_id: int):
    a = db.session.get(PDAttachment, attach_id)
    if not a or a.status != 1 or a.ref_type != "project":
        return ("Not Found", 404)

    actor_name = getattr(current_user, "username", "系統")
    _add_log(
        ref_type="project",
        ref_sid=a.ref_sid,
        action="DELETE",
        field_name="attachment",
        old_value=a.attachment_name,
        new_value=None,
        message=f"使用者「{actor_name}」刪除附件：{a.attachment_name}",
        actor_id=getattr(current_user, "user_sid", None),
    )

    a.status = 0
    a.update_usr = getattr(current_user, "user_sid", None)
    a.update_dt = datetime.utcnow()
    db.session.commit()

    flash("附件已刪除", "success")
    return redirect(url_for("project.project_detail", project_sid=a.ref_sid, tab="files", _anchor="pane-files"))
