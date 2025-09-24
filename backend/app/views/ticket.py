# -*- coding: utf-8 -*-
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app, send_file
from ..models import (
    db, PDTicket, Users, PDType, PDProject, PDCase, PDDispatch,
    PDComment, PDAttachment, PDActivityLog
)
from datetime import datetime
from flask_login import login_required, current_user
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
import os, uuid

ticket_bp = Blueprint('ticket', __name__, url_prefix='/tickets')

# =========================
# 共用輔助 / 常數
# =========================

# 狀態中文
STATUS_LABELS = {
    "open": "未受理",
    "received": "已受理",
    "processing": "處理中",
    "replied": "已回覆",
    "onhold": "暫停",
    "closed": "結案",
}
# 狀態下拉（值/顯示文字）
STATUS_OPTIONS = [
    ("open", "未受理"),
    ("received", "已受理"),
    ("processing", "處理中"),
    ("replied", "已回覆"),
    ("onhold", "暫停"),
    ("closed", "結案"),
]

# 優先級中文
PRIORITY_LABELS = {
    "low": "低",
    "normal": "一般",
    "high": "高",
    "urgent": "緊急",
}
# 優先級下拉（值/顯示文字）
PRIORITY_OPTIONS = [
    ("low", "低"),
    ("normal", "一般"),
    ("high", "高"),
    ("urgent", "緊急"),
]

# 來源對應
SOURCE_LABELS = {0: "未知", 1: "內部", 2: "外部"}


def _human_size(num_bytes: int) -> str:
    n = float(num_bytes)
    for unit in ['B','KB','MB','GB','TB']:
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



def _is_ajax(req: request) -> bool:
    return req.headers.get("X-Requested-With") == "XMLHttpRequest" or req.accept_mimetypes.best == "application/json"

def _gen_ticket_no():
    yyyymm = datetime.utcnow().strftime("%Y%m")
    prefix = f"CS-{yyyymm}"
    last_no = (
        db.session.query(PDTicket.ticket_no)
        .filter(PDTicket.ticket_no.like(f"{prefix}-%"))
        .order_by(PDTicket.ticket_no.desc())
        .first()
    )
    seq = 1
    if last_no and last_no[0]:
        try:
            seq = int(last_no[0].split("-")[-1]) + 1
        except Exception:
            seq = 1
    return f"{prefix}-{seq:03d}"

def _fmt(dt):
    if not dt: return "—"
    return dt.strftime("%Y/%m/%d %p%I:%M").replace("AM","上午").replace("PM","下午")

def _user_name(user_id):
    if not user_id: return "—"
    u = Users.query.get(user_id)
    return u.username if u else "—"

def _field_label(field_name: str) -> str:
    mapping = {
        "ticket_name": "標題",
        "ticket_description": "描述",
        "ticket_type": "類別",
        "project_sid": "所屬專案",
        "ticket_status": "狀態",
        "ticket_priority": "優先序",
        "memo": "備註",
        "ticket_no": "單號",
        "is_converted": "已轉案件",
    }
    return mapping.get(field_name, field_name)

def _value_display(field: str, val):
    if val is None:
        return "—"
    if field == "ticket_type":
        t = PDType.query.get(val) if str(val).isdigit() else None
        return t.type_name if t else "—"
    if field == "project_sid":
        p = PDProject.query.get(val) if str(val).isdigit() else None
        return p.project_name if p else "—"
    if field == "ticket_priority":
        return PRIORITY_LABELS.get(str(val).lower(), val)
    if field == "ticket_status":
        return STATUS_LABELS.get(str(val).lower(), val)
    if field == "is_converted":
        return "是" if (val is True or str(val) == "1") else "否"
    return str(val)

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

def _diff_and_log_ticket_changes(old_ticket: PDTicket, new_values: dict, actor_id: int, actor_name: str):
    fields = ["ticket_name", "ticket_description", "ticket_type",
              "project_sid", "ticket_status", "ticket_priority", "memo"]
    for f in fields:
        old = getattr(old_ticket, f)
        new = new_values.get(f, old)
        if str(old) != str(new):
            msg = f"使用者「{actor_name}」變更【{_field_label(f)}】：{_value_display(f, old)} → {_value_display(f, new)}"
            _add_log(
                ref_type="ticket",
                ref_sid=old_ticket.ticket_sid,
                action="UPDATE",
                field_name=f,
                old_value=old,
                new_value=new,
                message=msg,
                actor_id=actor_id
            )

def _timeline_item(row: PDActivityLog) -> dict:
    action_map = {"CREATE": "建立", "UPDATE": "更新", "COMMENT": "回覆", "DELETE": "刪除"}
    icon_map = {"CREATE": "plus", "UPDATE": "edit", "COMMENT": "message", "DELETE": "trash"}
    field = _field_label(row.field_name or "")
    old_txt = _value_display(row.field_name, row.old_value) if row.old_value is not None else ""
    new_txt = _value_display(row.field_name, row.new_value) if row.new_value is not None else ""
    who = _user_name(row.create_usr)
    when = _fmt(row.create_dt)
    msg = row.message or f"{who} {action_map.get(row.action, row.action)}了【{field}】：{old_txt} → {new_txt}"
    return {
        "id": row.activity_log_sid,
        "action": row.action,
        "action_label": action_map.get(row.action, row.action),
        "icon": icon_map.get(row.action, "log"),
        "field": field,
        "old": old_txt,
        "new": new_txt,
        "who": who,
        "when": when,
        "message": msg,
    }


# =========================
# 附件：上傳（含寫入歷程）
# =========================
@ticket_bp.route("/<int:ticket_sid>/attachments", methods=["POST"])
@login_required
def upload_attachment(ticket_sid: int):
    t = PDTicket.query.get_or_404(ticket_sid)
    actor_id = getattr(current_user, "user_sid", None)
    actor_name = getattr(current_user, "username", "系統")

    f = request.files.get("file")
    if not f or not f.filename.strip():
        flash("請選擇要上傳的檔案", "warning")
        return redirect(url_for("ticket.detail", ticket_sid=ticket_sid, tab="files", _anchor="pane-files"))

    # 寫檔
    root = _upload_root()
    rel_dir = os.path.join("tickets", str(ticket_sid))
    abs_dir = os.path.join(root, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)

    ext = os.path.splitext(f.filename)[1]
    safe_name = f"{uuid.uuid4().hex}{ext}"
    abs_path = os.path.join(abs_dir, safe_name)
    f.save(abs_path)

    size = os.path.getsize(abs_path)

    a = PDAttachment(
        ref_type="ticket",
        ref_sid=ticket_sid,
        attachment_code=os.path.join(rel_dir, safe_name).replace("\\", "/"),
        attachment_name=f.filename,
        attachment_size=_human_size(size),
        status=1,
        create_usr=actor_id,
        create_dt=datetime.utcnow(),
    )
    db.session.add(a)
    db.session.flush()  # 先拿到 PK 再寫歷程

    # ★ 寫入歷程：上傳附件
    _add_log(
        ref_type="ticket",
        ref_sid=ticket_sid,
        action="CREATE",
        field_name="attachment",
        old_value=None,
        new_value=a.attachment_name,
        message=f"使用者「{actor_name}」上傳附件：{a.attachment_name}（{a.attachment_size}）",
        actor_id=actor_id
    )

    db.session.commit()
    flash("檔案已上傳", "success")
    return redirect(url_for("ticket.detail", ticket_sid=ticket_sid, tab="files", _anchor="pane-files"))


# =========================
# 附件：下載
# =========================
@ticket_bp.route("/attachments/<int:attach_id>/download", methods=["GET"])
@login_required
def download_attachment(attach_id: int):
    a = db.session.get(PDAttachment, attach_id)
    # 僅允許客服單附件
    if not a or a.status != 1 or a.ref_type != "ticket":
        return ("Not Found", 404)

    root = _upload_root()
    abs_path = os.path.join(root, a.attachment_code)
    if not os.path.exists(abs_path):
        return ("Not Found", 404)

    return send_file(abs_path, as_attachment=True, download_name=a.attachment_name)


# =========================
# 附件：刪除（含寫入歷程）
# =========================
@ticket_bp.route("/attachments/<int:attach_id>/delete", methods=["POST"])
@login_required
def delete_attachment(attach_id: int):
    a = db.session.get(PDAttachment, attach_id)
    if not a or a.status != 1 or a.ref_type != "ticket":
        return ("Not Found", 404)

    actor_id = getattr(current_user, "user_sid", None)
    actor_name = getattr(current_user, "username", "系統")

    # ★ 先寫入歷程：刪除附件
    _add_log(
        ref_type="ticket",
        ref_sid=a.ref_sid,
        action="DELETE",
        field_name="attachment",
        old_value=a.attachment_name,
        new_value=None,
        message=f"使用者「{actor_name}」刪除附件：{a.attachment_name}",
        actor_id=actor_id
    )

    # 再做軟刪除
    a.status = 0
    a.update_usr = actor_id
    a.update_dt = datetime.utcnow()
    db.session.commit()

    flash("附件已刪除", "success")
    return redirect(url_for("ticket.detail", ticket_sid=a.ref_sid, tab="files", _anchor="pane-files"))



# =========================
# 明細頁
# =========================

@ticket_bp.route("/<int:ticket_sid>")
@login_required
def detail(ticket_sid: int):
    t = PDTicket.query.filter_by(ticket_sid=ticket_sid).first_or_404()
    cat = PDType.query.get(t.ticket_type)
    type_name = cat.type_name if cat else "—"
    project = PDProject.query.get(t.project_sid)

    case = (PDCase.query.filter_by(ticket_sid=t.ticket_sid)
            .order_by(PDCase.create_dt.desc()).first())
    dispatch = None
    if case:
        dispatch = (PDDispatch.query.filter_by(case_sid=case.case_sid)
                    .order_by(PDDispatch.create_dt.desc()).first())

    comment_rows = (
        db.session.query(PDComment, Users.username)
        .outerjoin(Users, Users.user_sid == PDComment.author_sid)
        .filter(PDComment.ref_type == 'ticket', PDComment.ref_sid == t.ticket_sid)
        .order_by(PDComment.create_dt.desc())
        .all()
    )
    comments = [
        {"author_name": (uname or "系統"), "content": c.content, "create_dt": c.create_dt}
        for c, uname in comment_rows
    ]

    attachments = (PDAttachment.query
                   .filter_by(ref_type='ticket', ref_sid=t.ticket_sid)
                   .order_by(PDAttachment.create_dt.desc())
                   .all())

    logs = (PDActivityLog.query
            .filter_by(ref_type="ticket", ref_sid=t.ticket_sid, status=1)
            .order_by(PDActivityLog.create_dt.desc()).all())

    timeline = [_timeline_item(r) for r in logs]

    context = dict(
        t=t,
        type_name=type_name,
        project=project,
        case=case,
        dispatch=dispatch,
        comments=comments,
        attachments=attachments,
        logs=logs,
        timeline=timeline,
        created_by=_user_name(t.create_usr),
        received_at=_fmt(t.create_dt),
        case_due=_fmt(case.due_dt) if case else "—",
        case_no=case.case_no if case else "—",
        dispatch_name=dispatch.dispatch_name if dispatch else "—",
        dispatch_status=dispatch.dispatch_status if dispatch else "—",
        dispatch_when=_fmt(dispatch.scheduled_dt) if dispatch else "—",
    )
    return render_template("ticket_detail.html", **context)

# =========================
# 回覆（留言）
# =========================

@ticket_bp.route("/<int:ticket_sid>/comment", methods=["POST"])
@login_required
def add_comment(ticket_sid: int):
    from_project = request.args.get("from_project")
    content = (request.form.get("content") or "").strip()
    actor_id = getattr(current_user, "user_sid", None)
    actor_name = getattr(current_user, "username", "系統")

    if not content:
        flash("請輸入內容", "warning")
        return redirect(url_for("ticket.detail", ticket_sid=ticket_sid, from_project=from_project) if from_project
                        else url_for("ticket.detail", ticket_sid=ticket_sid))

    try:
        c = PDComment(
            ref_type="ticket",
            ref_sid=ticket_sid,
            author_sid=actor_id,
            content=content,
            status=1,
            create_usr=actor_id,
            create_dt=datetime.utcnow(),
        )
        db.session.add(c)

        _add_log(
            ref_type="ticket",
            ref_sid=ticket_sid,
            action="COMMENT",
            field_name="comment",
            old_value=None,
            new_value=None,
            message=f"使用者「{actor_name}」新增了回覆：{content[:120]}",
            actor_id=actor_id
        )

        db.session.commit()
        flash("已新增回覆", "success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("新增回覆失敗：%s", e)
        flash("新增回覆失敗", "danger")

    return redirect(url_for("ticket.detail", ticket_sid=ticket_sid, from_project=from_project) if from_project
                    else url_for("ticket.detail", ticket_sid=ticket_sid))

# =========================
# 列表
# =========================

@ticket_bp.route("/", methods=["GET"])
@login_required
def ticket_list():
    log = current_app.logger
    try:
        q = request.args.get("q", "").strip()
        status_id = request.args.get("status_id")
        type_id = request.args.get("type_id")

        stmt = (
            db.session.query(
                PDTicket,
                Users.username.label("creator_name"),
                PDType.type_name.label("type_name")
            )
            .join(Users, Users.user_sid == PDTicket.create_usr)
            .join(PDType, PDType.type_sid == PDTicket.ticket_type, isouter=True)
            .order_by(PDTicket.create_dt.desc())
        )

        if q:
            stmt = stmt.filter(
                (PDTicket.ticket_no.like(f"%{q}%")) |
                (PDTicket.ticket_name.like(f"%{q}%"))
            )
        if status_id:
            stmt = stmt.filter(PDTicket.ticket_status == status_id)
        if type_id:
            stmt = stmt.filter(PDTicket.ticket_type == type_id)

        rows = stmt.all()

        tickets = []
        for r in rows:
            t = r[0]
            tickets.append({
                "id": t.ticket_sid,
                "code": t.ticket_no,
                "subject": t.ticket_name,
                "status": t.ticket_status,
                "status_label": STATUS_LABELS.get(t.ticket_status, t.ticket_status),
                "creator": r.creator_name,
                "type": r.type_name or "-",
                "created_at": t.create_dt,
            })

        types = (PDType.query
                 .filter(PDType.status == 1, PDType.type_group == 'ticket')
                 .order_by(PDType.type_name.asc())
                 .all())

        projects = PDProject.query.filter_by(status=1).order_by(PDProject.project_name.asc()).all()
        users = Users.query.filter_by(status=1).order_by(Users.username.asc()).all()

        return render_template(
            "tickets.html",
            tickets=tickets,
            statuses=STATUS_OPTIONS,
            types=types,
            projects=projects,
            users=users,
        )

    except Exception as e:
        log.exception("查詢 tickets 失敗：%s", e)
        return ("Internal Error", 500)

# =========================
# 建立
# =========================


@ticket_bp.route('/create', methods=['GET', 'POST'])
@login_required
def ticket_create():
    is_xhr = _is_ajax(request)
    log = current_app.logger

    # ========= GET：渲染建立表單 =========
    if request.method == 'GET':
        try:
            projects = PDProject.query.filter_by(status=1).order_by(PDProject.project_name.asc()).all()
            types    = (PDType.query
                        .filter(PDType.status == 1, PDType.type_group == 'ticket')
                        .order_by(PDType.type_name.asc())
                        .all())
            users    = Users.query.filter_by(status=1).order_by(Users.username.asc()).all()

            preselect_project_sid = request.args.get("project_sid", type=int)
            return render_template(
                'ticket_form.html',
                mode='create',
                projects=projects,
                types=types,
                users=users,
                status_options=STATUS_OPTIONS,
                priority_options=PRIORITY_OPTIONS,
                preselect_project_sid=preselect_project_sid
            )
        except Exception as e:
            log.exception("載入建立頁失敗：%s", e)
            return redirect(url_for('ticket.ticket_list'))

    # ========= POST：建立 =========
    try:
        ticket_name        = (request.form.get('ticket_name') or '').strip()
        ticket_description = (request.form.get('ticket_description') or '').strip()
        ticket_type_raw    = request.form.get('ticket_type')
        project_sid_raw    = request.form.get('project_sid')
        create_usr         = current_user.user_sid if hasattr(current_user, "user_sid") else None

        ticket_no       = (request.form.get('ticket_no') or '').strip() or _gen_ticket_no()
        # ★ 來源一律外部：忽略任何前端值
        ticket_status   = (request.form.get('ticket_status') or "open").strip()
        ticket_priority = (request.form.get('ticket_priority') or "normal").strip()
        memo            = (request.form.get('memo') or '').strip()

        if not ticket_name:
            msg = "標題不得為空"
            return (jsonify(ok=False, message=msg), 400) if is_xhr else (flash(msg, "warning"), redirect(url_for("ticket.ticket_create")))[1]
        if not project_sid_raw:
            msg = "請選擇關聯專案"
            return (jsonify(ok=False, message=msg), 400) if is_xhr else (flash(msg, "warning"), redirect(url_for("ticket.ticket_create")))[1]
        if not create_usr:
            msg = "未取得登入者，請重新登入"
            return (jsonify(ok=False, message=msg), 401) if is_xhr else (flash(msg, "danger"), redirect(url_for("ticket.ticket_list")))[1]

        new_ticket = PDTicket(
            project_sid   = int(project_sid_raw),
            ticket_name   = ticket_name,
            ticket_no     = ticket_no,
            source_type   = 2,  # ★ 固定外部
            ticket_type   = int(ticket_type_raw) if (ticket_type_raw or "").isdigit() else None,
            ticket_description = ticket_description,
            ticket_status = ticket_status,
            ticket_priority = ticket_priority,
            memo          = memo,
            status        = 1,
            create_usr    = int(create_usr),
            create_dt     = datetime.utcnow()
        )
        db.session.add(new_ticket)
        db.session.flush()

        _add_log(
            ref_type="ticket",
            ref_sid=new_ticket.ticket_sid,
            action="CREATE",
            field_name="ticket_no",
            old_value=None,
            new_value=new_ticket.ticket_no,
            message=f"使用者「{_user_name(create_usr)}」建立客服單（單號：{new_ticket.ticket_no}）",
            actor_id=create_usr
        )
        db.session.commit()

        from_project_sid = request.form.get("from_project_sid", type=int)
        if is_xhr:
            return jsonify(ok=True, id=new_ticket.ticket_sid, code=new_ticket.ticket_no)

        if from_project_sid:
            return redirect(url_for(
                "project.project_detail",
                project_sid=from_project_sid,
                tab="tickets",
                ticket_sort="created_at",
                ticket_dir="desc",
                _anchor="pane-tickets"
            ))

        flash("客服單已建立", "success")
        return redirect(url_for("ticket.ticket_list"))

    except Exception as e:
        db.session.rollback()
        log.exception("建立客服單失敗：%s", e)
        if is_xhr:
            return jsonify(ok=False, message=str(e)), 500
        flash(f"建立失敗：{e}", "danger")
        return redirect(url_for("ticket.ticket_create"))


#
# @ticket_bp.route('/create', methods=['GET', 'POST'])
# @login_required
# def ticket_create():
#     is_xhr = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json
#     log = current_app.logger
#
#     if request.method == 'GET':
#         if is_xhr: return jsonify(ok=True)
#         return redirect(url_for('ticket.ticket_list'))
#
#     try:
#         ticket_name        = (request.form.get('ticket_name') or '').strip()
#         ticket_description = (request.form.get('ticket_description') or '').strip()
#         ticket_type_raw    = request.form.get('ticket_type')
#         project_sid_raw    = request.form.get('project_sid')
#         create_usr         = current_user.user_sid if hasattr(current_user, "user_sid") else None
#
#         ticket_no       = (request.form.get('ticket_no') or '').strip() or _gen_ticket_no()
#         source_type_raw = request.form.get('source_type') or 0
#         ticket_status   = (request.form.get('ticket_status') or "open").strip()
#         ticket_priority = (request.form.get('ticket_priority') or "normal").strip()
#         memo            = (request.form.get('memo') or '').strip()
#
#         if not ticket_name:
#             return jsonify(ok=False, message="標題不得為空"), 400
#         if not project_sid_raw:
#             return jsonify(ok=False, message="請選擇關聯專案"), 400
#         if not create_usr:
#             return jsonify(ok=False, message="未取得登入者，請重新登入"), 401
#
#         new_ticket = PDTicket(
#             project_sid   = int(project_sid_raw),
#             ticket_name   = ticket_name,
#             ticket_no     = ticket_no,
#             source_type   = int(source_type_raw) if str(source_type_raw).isdigit() else 0,
#             ticket_type   = int(ticket_type_raw) if (ticket_type_raw or "").isdigit() else None,
#             ticket_description = ticket_description,
#             ticket_status = ticket_status,
#             ticket_priority = ticket_priority,
#             memo          = memo,
#             status        = 1,
#             create_usr    = int(create_usr),
#             create_dt     = datetime.utcnow()
#         )
#         db.session.add(new_ticket)
#         db.session.flush()
#
#         _add_log(
#             ref_type="ticket",
#             ref_sid=new_ticket.ticket_sid,
#             action="CREATE",
#             field_name="ticket_no",
#             old_value=None,
#             new_value=new_ticket.ticket_no,
#             message=f"使用者「{_user_name(create_usr)}」建立客服單（單號：{new_ticket.ticket_no}）",
#             actor_id=create_usr
#         )
#
#         db.session.commit()
#
#         return jsonify(ok=True, id=new_ticket.ticket_sid, code=new_ticket.ticket_no)
#
#     except Exception as e:
#         db.session.rollback()
#         log.exception("建立客服單失敗：%s", e)
#         return jsonify(ok=False, message=str(e)), 500

# =========================
# 編輯（含差異歷程）
# =========================

@ticket_bp.route('/edit/<int:ticket_sid>', methods=['GET', 'POST'])
@login_required
def ticket_edit(ticket_sid):
    log = current_app.logger
    ticket = PDTicket.query.get_or_404(ticket_sid)

    if request.method == 'GET':
        if _is_ajax(request):
            return jsonify({
                "ok": True,
                "ticket": {
                    "id": ticket.ticket_sid,
                    "ticket_name": ticket.ticket_name or "",
                    "ticket_description": ticket.ticket_description or "",
                    "ticket_type": ticket.ticket_type,
                    "project_sid": ticket.project_sid,
                    "ticket_status": ticket.ticket_status,
                    "ticket_priority": ticket.ticket_priority,
                    "memo": ticket.memo or ""
                }
            })

        projects = PDProject.query.filter_by(status=1).all()
        types    = (PDType.query
                    .filter(PDType.status == 1, PDType.type_group == 'ticket')
                    .order_by(PDType.type_name.asc())
                    .all())
        users    = Users.query.filter_by(status=1).all()

        # ★ 將狀態與優先級選項（中文）傳給模板
        return render_template(
            'ticket_form.html',
            mode='edit',
            ticket=ticket,
            projects=projects,
            types=types,
            users=users,
            status_options=STATUS_OPTIONS,
            priority_options=PRIORITY_OPTIONS
        )

    try:
        actor_id = getattr(current_user, "user_sid", None)
        actor_name = getattr(current_user, "username", "系統")

        new_vals = {}

        if 'ticket_name' in request.form:
            new_vals['ticket_name'] = (request.form.get('ticket_name') or ticket.ticket_name or '').strip()
            ticket.ticket_name = new_vals['ticket_name']

        if 'ticket_description' in request.form:
            new_vals['ticket_description'] = (request.form.get('ticket_description') or '').strip()
            ticket.ticket_description = new_vals['ticket_description']

        ticket_type_raw = request.form.get('ticket_type')
        if ticket_type_raw is not None:
            if ticket_type_raw == "":
                new_vals['ticket_type'] = None
                ticket.ticket_type = None
            else:
                new_vals['ticket_type'] = int(ticket_type_raw)
                ticket.ticket_type = new_vals['ticket_type']

        project_sid_raw = request.form.get('project_sid')
        if project_sid_raw is not None and project_sid_raw != "":
            new_vals['project_sid'] = int(project_sid_raw)
            ticket.project_sid = new_vals['project_sid']

        if 'ticket_status' in request.form:
            new_vals['ticket_status'] = (request.form['ticket_status'] or ticket.ticket_status).strip()
            ticket.ticket_status = new_vals['ticket_status']

        if 'ticket_priority' in request.form:
            new_vals['ticket_priority'] = (request.form['ticket_priority'] or ticket.ticket_priority).strip()
            ticket.ticket_priority = new_vals['ticket_priority']

        if 'memo' in request.form:
            new_vals['memo'] = (request.form.get('memo') or '').strip()
            ticket.memo = new_vals['memo']

        ticket.update_usr = actor_id
        ticket.update_dt = datetime.utcnow()

        _diff_and_log_ticket_changes(ticket, new_vals, actor_id, actor_name)

        db.session.commit()

        if _is_ajax(request):
            return jsonify({"ok": True, "message": "已更新", "id": ticket.ticket_sid})

        flash('客服單已更新', 'success')
        return redirect(url_for('ticket.ticket_list'))
    except Exception as e:
        db.session.rollback()
        log.exception("更新 ticket 失敗 id=%s: %s", ticket_sid, e)
        if _is_ajax(request):
            return jsonify({"ok": False, "message": f"更新失敗：{e}"}), 400
        flash(f'更新失敗：{str(e)}', 'danger')
        return redirect(url_for('ticket.ticket_list'))

# =========================
# 刪除
# =========================

@ticket_bp.route('/delete/<int:ticket_sid>', methods=['POST'])
@login_required
def ticket_delete(ticket_sid):
    try:
        actor_id = getattr(current_user, "user_sid", None)
        actor_name = getattr(current_user, "username", "系統")
        ticket = PDTicket.query.get_or_404(ticket_sid)

        _add_log(
            ref_type="ticket",
            ref_sid=ticket.ticket_sid,
            action="DELETE",
            field_name="ticket_no",
            old_value=ticket.ticket_no,
            new_value=None,
            message=f"使用者「{actor_name}」刪除了客服單（單號：{ticket.ticket_no}）",
            actor_id=actor_id
        )

        db.session.delete(ticket)
        db.session.commit()
        flash('客服單已刪除', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'刪除失敗：{str(e)}', 'danger')
    return redirect(url_for('ticket.ticket_list'))

# =========================
# 結案
# =========================

@ticket_bp.route('/<int:ticket_sid>/close', methods=['POST'])
@login_required
def close_ticket(ticket_sid: int):
    from_project = request.args.get("from_project")
    is_ajax = _is_ajax(request)
    log = current_app.logger

    t = PDTicket.query.get_or_404(ticket_sid)
    old_status = t.ticket_status or ""
    actor_id = getattr(current_user, "user_sid", None)
    actor_name = getattr(current_user, "username", "系統")

    if old_status.lower() == "closed":
        msg = "此客服單已為結案狀態"
        if is_ajax:
            return jsonify(ok=True, message=msg, id=ticket_sid, status="closed")
        flash(msg, "info")
        return redirect(url_for("ticket.detail", ticket_sid=ticket_sid, from_project=from_project) if from_project
                        else url_for("ticket.detail", ticket_sid=ticket_sid))

    try:
        t.ticket_status = "closed"
        t.update_usr = actor_id
        t.update_dt = datetime.utcnow()
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        msg = f"結案失敗：{e}"
        if is_ajax:
            return jsonify(ok=False, message=msg), 500
        flash(msg, "danger")
        return redirect(url_for("ticket.detail", ticket_sid=ticket_sid, from_project=from_project) if from_project
                        else url_for("ticket.detail", ticket_sid=ticket_sid))

    try:
        row = PDActivityLog(
            ref_type="ticket",
            ref_sid=t.ticket_sid,
            action="UPDATE",
            field_name="ticket_status",
            old_value=old_status,
            new_value="closed",
            message=f"使用者「{actor_name}」將客服單結案",
            status=1,
            create_usr=actor_id,
            create_dt=datetime.utcnow(),
        )
        db.session.add(row)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        try:
            next_id = (db.session.query(func.coalesce(func.max(PDActivityLog.activity_log_sid), 0)).scalar() or 0) + 1
            row = PDActivityLog(
                activity_log_sid=next_id,
                ref_type="ticket",
                ref_sid=t.ticket_sid,
                action="UPDATE",
                field_name="ticket_status",
                old_value=old_status,
                new_value="closed",
                message=f"使用者「{actor_name}」將客服單結案",
                status=1,
                create_usr=actor_id,
                create_dt=datetime.utcnow(),
            )
            db.session.add(row)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()

    if is_ajax:
        return jsonify(ok=True, message="已結案", id=ticket_sid, status="closed")

    flash("已將客服單結案", "success")
    return redirect(url_for("ticket.detail", ticket_sid=ticket_sid, from_project=from_project) if from_project
                    else url_for("ticket.detail", ticket_sid=ticket_sid))
