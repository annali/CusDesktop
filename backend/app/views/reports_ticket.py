from __future__ import annotations
from datetime import datetime, timedelta, date
from typing import Dict, Any, List, Tuple

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required

from ..models import db, PDTicket, PDProject, PDType

reports_ticket_bp = Blueprint("reports_ticket", __name__, url_prefix="/reports")

# ------------------------ 共用：日期解析 ------------------------
def _parse_date(s: str | None, end: bool = False):
    if not s:
        return None
    try:
        d = datetime.strptime(s, "%Y-%m-%d")
        if end:
            return d.replace(hour=23, minute=59, second=59, microsecond=999999)
        return d.replace(hour=0, minute=0, second=0, microsecond=0)
    except Exception:
        return None

def _default_range_if_empty(date_from: datetime | None, date_to: datetime | None, days: int = 30) -> Tuple[datetime, datetime]:
    """若未帶日期，預設近 N 天（含當日）。"""
    if not date_to:
        now = datetime.utcnow()
        date_to = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    if not date_from:
        date_from = (date_to - timedelta(days=days-1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return date_from, date_to

# ================================================================
# =============== 客服單狀態分布（保留） =========================
# ================================================================
def _query_status_distribution(params: Dict[str, Any]) -> Dict[str, Any]:
    q = db.session.query(
        PDTicket.ticket_status,
        db.func.count(PDTicket.ticket_sid).label("cnt")
    ).filter(PDTicket.status == 1)

    if params.get("project_sid"):
        q = q.filter(PDTicket.project_sid == params["project_sid"])
    if params.get("date_from"):
        q = q.filter(PDTicket.create_dt >= params["date_from"])
    if params.get("date_to"):
        q = q.filter(PDTicket.create_dt <= params["date_to"])

    q = q.group_by(PDTicket.ticket_status).order_by(db.desc("cnt"))
    rows = q.all()

    labels, counts, total = [], [], 0
    for st, cnt in rows:
        labels.append(st or "未設定")
        counts.append(int(cnt)); total += int(cnt)

    def palette(n: int) -> List[str]:
        return [f"hsl({(i*29)%360} 55% 55%)" for i in range(max(n, 0))]

    return {
        "labels": labels,
        "counts": counts,
        "total": total,
        "palette": palette(len(labels)),
        "rows": [
            {"status": labels[i], "count": counts[i], "percent": (counts[i]/total*100.0) if total else 0.0}
            for i in range(len(labels))
        ]
    }

@reports_ticket_bp.route("/tickets/status", methods=["GET"])
@login_required
def tickets_status_page():
    project_sid = request.args.get("project_sid", type=int)
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)

    params = {"project_sid": project_sid, "date_from": date_from, "date_to": date_to}
    chart_data = _query_status_distribution(params)
    projects = PDProject.query.filter(PDProject.status == 1).order_by(PDProject.project_name.asc()).all()
    raw_params = {
        "project_sid": project_sid or "",
        "date_from": request.args.get("date_from", "", type=str),
        "date_to": request.args.get("date_to", "", type=str),
    }
    return render_template("reports/tickets_status.html",
        ACTIVE_MENU="reports", ACTIVE_SUBMENU="tickets", ACTIVE_ITEM="tickets_status",
        projects=projects, params=raw_params, chart_data=chart_data)

@reports_ticket_bp.route("/tickets/status/data", methods=["GET"])
@login_required
def tickets_status_data():
    project_sid = request.args.get("project_sid", type=int)
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    params = {"project_sid": project_sid, "date_from": date_from, "date_to": date_to}
    return jsonify(_query_status_distribution(params))

# ================================================================
# =================== 客服單進度趨勢（保留） =====================
# ================================================================
DONE_KEYWORDS = [
    "done", "closed", "resolve", "resolved", "finish", "finished", "complete", "completed",
    "完成", "已完成", "結案", "已結案", "關閉", "已關閉", "已處理", "已解決"
]
_DONE_KEYWORDS_LOWER = [k.lower() for k in DONE_KEYWORDS]
def _is_done(status: str | None) -> bool:
    s = (status or "").lower()
    return any(k in s for k in _DONE_KEYWORDS_LOWER)

def _done_sql_expr():
    """提供 SQL 條件：ticket_status LIKE %keyword%（忽略大小寫）"""
    return db.or_(*[db.func.lower(PDTicket.ticket_status).like(f"%{k}%") for k in _DONE_KEYWORDS_LOWER])

def _iter_buckets(date_from: datetime, date_to: datetime, interval: str) -> List[date]:
    buckets: List[date] = []
    if interval == "month":
        cur = date(date_from.year, date_from.month, 1)
        end = date(date_to.year, date_to.month, 1)
        while cur <= end:
            buckets.append(cur)
            cur = date(cur.year + (1 if cur.month == 12 else 0),
                       1 if cur.month == 12 else cur.month + 1, 1)
    elif interval == "week":
        start = (date_from.date() - timedelta(days=date_from.weekday()))
        end = (date_to.date() - timedelta(days=date_to.weekday()))
        cur = start
        while cur <= end:
            buckets.append(cur); cur = cur + timedelta(days=7)
    else:
        cur = date_from.date(); end = date_to.date()
        while cur <= end:
            buckets.append(cur); cur = cur + timedelta(days=1)
    return buckets

def _bucket_key(dt: datetime, interval: str) -> date:
    if interval == "month": return date(dt.year, dt.month, 1)
    if interval == "week":
        d = dt.date(); return d - timedelta(days=d.weekday())
    return dt.date()

def _format_label(d: date, interval: str) -> str:
    if interval == "month": return d.strftime("%Y-%m")
    if interval == "week":
        iso = d.isocalendar(); return f"{d.strftime('%Y-%m-%d')} (W{iso.week:02d})"
    return d.strftime("%Y-%m-%d")

def _query_trend(params: Dict[str, Any]) -> Dict[str, Any]:
    project_sid: int | None = params.get("project_sid")
    date_from: datetime = params["date_from"]
    date_to: datetime = params["date_to"]
    interval: str = params.get("interval") or "day"

    bucket_starts: List[date] = _iter_buckets(date_from, date_to, interval)
    labels = [_format_label(b, interval) for b in bucket_starts]
    idx = {b: i for i, b in enumerate(bucket_starts)}
    created_series = [0] * len(bucket_starts)
    closed_series = [0] * len(bucket_starts)

    q_new = db.session.query(PDTicket.create_dt).filter(
        PDTicket.status == 1, PDTicket.create_dt >= date_from, PDTicket.create_dt <= date_to
    )
    if project_sid: q_new = q_new.filter(PDTicket.project_sid == project_sid)
    for (dt,) in q_new.all():
        key = _bucket_key(dt, interval)
        if key in idx: created_series[idx[key]] += 1

    q_close = db.session.query(PDTicket.update_dt, PDTicket.ticket_status).filter(
        PDTicket.status == 1, PDTicket.update_dt >= date_from, PDTicket.update_dt <= date_to
    )
    if project_sid: q_close = q_close.filter(PDTicket.project_sid == project_sid)
    for dt, st in q_close.all():
        if not _is_done(st): continue
        key = _bucket_key(dt, interval)
        if key in idx: closed_series[idx[key]] += 1

    return {
        "labels": labels,
        "series": [{"name": "新增", "data": created_series}, {"name": "完成", "data": closed_series}],
        "totals": {"created": sum(created_series), "closed": sum(closed_series)},
    }

@reports_ticket_bp.route("/tickets/trend", methods=["GET"])
@login_required
def tickets_trend_page():
    project_sid = request.args.get("project_sid", type=int)
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    date_from, date_to = _default_range_if_empty(date_from, date_to)
    interval = request.args.get("interval", "day")
    if interval not in ("day", "week", "month"): interval = "day"

    params = {"project_sid": project_sid, "date_from": date_from, "date_to": date_to, "interval": interval}
    chart_data = _query_trend(params)
    projects = PDProject.query.filter(PDProject.status == 1).order_by(PDProject.project_name.asc()).all()
    raw_params = {
        "project_sid": project_sid or "", "date_from": date_from.strftime("%Y-%m-%d"),
        "date_to": date_to.strftime("%Y-%m-%d"), "interval": interval,
    }
    return render_template("reports/tickets_trend.html",
        ACTIVE_MENU="reports", ACTIVE_SUBMENU="tickets", ACTIVE_ITEM="tickets_trend",
        projects=projects, params=raw_params, chart_data=chart_data)

@reports_ticket_bp.route("/tickets/trend/data", methods=["GET"])
@login_required
def tickets_trend_data():
    project_sid = request.args.get("project_sid", type=int)
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    date_from, date_to = _default_range_if_empty(date_from, date_to)
    interval = request.args.get("interval", "day")
    if interval not in ("day", "week", "month"): interval = "day"
    params = {"project_sid": project_sid, "date_from": date_from, "date_to": date_to, "interval": interval}
    return jsonify(_query_trend(params))

# ================================================================
# =================== 客服單處理效率（保留） =====================
# ================================================================
_BUCKETS = [
    (0,        60*60,              "< 1 小時"),
    (60*60,    4*60*60,            "1–4 小時"),
    (4*60*60,  8*60*60,            "4–8 小時"),
    (8*60*60,  24*60*60,           "8–24 小時"),
    (24*60*60, 3*24*60*60,         "1–3 天"),
    (3*24*60*60, 7*24*60*60,       "3–7 天"),
    (7*24*60*60, float("inf"),     "≥ 7 天"),
]
def _percentile(values: List[float], p: float) -> float:
    if not values: return 0.0
    s = sorted(values); k = (len(s)-1) * p; f = int(k); c = min(f+1, len(s)-1)
    if f == c: return s[int(k)]
    return s[f] + (s[c]-s[f])*(k-f)

def _query_efficiency(params: Dict[str, Any]) -> Dict[str, Any]:
    project_sid: int | None = params.get("project_sid")
    date_from: datetime = params["date_from"]
    date_to: datetime = params["date_to"]

    q_done = db.session.query(
        PDTicket.ticket_sid, PDTicket.ticket_no, PDTicket.ticket_name,
        PDTicket.project_sid, PDTicket.create_dt, PDTicket.update_dt, PDTicket.ticket_status
    ).filter(
        PDTicket.status == 1, PDTicket.update_dt >= date_from, PDTicket.update_dt <= date_to
    )
    if project_sid: q_done = q_done.filter(PDTicket.project_sid == project_sid)

    done_secs: List[float] = []
    bucket_counts = [0] * len(_BUCKETS)
    samples: List[Dict[str, Any]] = []

    for row in q_done.order_by(PDTicket.update_dt.desc()).limit(2000).all():
        if not _is_done(row.ticket_status): continue
        if not (row.update_dt and row.create_dt): continue
        sec = (row.update_dt - row.create_dt).total_seconds()
        if sec < 0: continue
        done_secs.append(sec)
        for i, (lo, hi, _) in enumerate(_BUCKETS):
            if lo <= sec < hi: bucket_counts[i] += 1; break
        if len(samples) < 50:
            samples.append({
                "ticket_sid": row.ticket_sid, "ticket_no": row.ticket_no, "ticket_name": row.ticket_name,
                "project_sid": row.project_sid, "created_at": row.create_dt.strftime("%Y-%m-%d %H:%M"),
                "resolved_at": row.update_dt.strftime("%Y-%m-%d %H:%M"), "resolved_hours": round(sec/3600.0, 2)
            })

    done_count = len(done_secs)
    avg_hours = round((sum(done_secs)/done_count)/3600.0, 2) if done_count else 0.0
    median_hours = round((_percentile(done_secs, 0.5))/3600.0, 2) if done_count else 0.0
    p90_hours = round((_percentile(done_secs, 0.9))/3600.0, 2) if done_count else 0.0

    now = datetime.utcnow()
    q_open = db.session.query(PDTicket.create_dt, PDTicket.ticket_status).filter(
        PDTicket.status == 1, PDTicket.create_dt >= date_from, PDTicket.create_dt <= date_to
    )
    if project_sid: q_open = q_open.filter(PDTicket.project_sid == project_sid)

    open_secs: List[float] = []
    for (c_dt, st) in q_open.all():
        if _is_done(st): continue
        age = (now - c_dt).total_seconds()
        if age >= 0: open_secs.append(age)

    open_count = len(open_secs)
    open_avg_age_hours = round((sum(open_secs)/open_count)/3600.0, 2) if open_count else 0.0

    labels = [b[2] for b in _BUCKETS]
    total_for_pct = done_count if done_count else 1
    buckets = [
        {"label": labels[i], "count": bucket_counts[i], "pct": round(bucket_counts[i]/total_for_pct*100.0, 2)}
        for i in range(len(labels))
    ]
    return {
        "buckets": buckets,
        "series": {"labels": labels, "data": bucket_counts},
        "kpi": {
            "done_count": done_count, "avg_hours": avg_hours, "median_hours": median_hours, "p90_hours": p90_hours,
            "open_count": open_count, "open_avg_age_hours": open_avg_age_hours
        },
        "samples": samples
    }

@reports_ticket_bp.route("/tickets/efficiency", methods=["GET"])
@login_required
def tickets_efficiency_page():
    project_sid = request.args.get("project_sid", type=int)
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    date_from, date_to = _default_range_if_empty(date_from, date_to)
    params = {"project_sid": project_sid, "date_from": date_from, "date_to": date_to}
    chart_data = _query_efficiency(params)
    projects = PDProject.query.filter(PDProject.status == 1).order_by(PDProject.project_name.asc()).all()
    raw_params = {
        "project_sid": project_sid or "", "date_from": date_from.strftime("%Y-%m-%d"),
        "date_to": date_to.strftime("%Y-%m-%d"),
    }
    return render_template("reports/tickets_efficiency.html",
        ACTIVE_MENU="reports", ACTIVE_SUBMENU="tickets", ACTIVE_ITEM="tickets_eff",
        projects=projects, params=raw_params, chart_data=chart_data)

@reports_ticket_bp.route("/tickets/efficiency/data", methods=["GET"])
@login_required
def tickets_efficiency_data():
    project_sid = request.args.get("project_sid", type=int)
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    date_from, date_to = _default_range_if_empty(date_from, date_to)
    params = {"project_sid": project_sid, "date_from": date_from, "date_to": date_to}
    return jsonify(_query_efficiency(params))

# ================================================================
# ====================== 專案總覽（修正：row 補 p90） ============
# ================================================================
def _palette(n: int) -> List[str]:
    return [f"hsl({(i*37)%360} 60% 52%)" for i in range(max(n,0))]

def _query_projects_overview(params: Dict[str, Any]) -> Dict[str, Any]:
    date_from: datetime = params["date_from"]
    date_to  : datetime = params["date_to"]
    statuses: List[str] | None = params.get("statuses")
    kw: str | None = params.get("kw")

    q_proj = PDProject.query.filter(PDProject.status == 1)
    if statuses:
        q_proj = q_proj.filter(PDProject.project_status.in_(statuses))
    if kw:
        like = f"%{kw.strip()}%"
        q_proj = q_proj.filter(db.or_(PDProject.project_name.ilike(like),
                                      PDProject.project_no.ilike(like)))
    projects: List[PDProject] = q_proj.order_by(PDProject.project_name.asc()).all()
    proj_ids = [p.project_sid for p in projects]

    if not projects:
        return {
            "kpi": {"projects": 0, "open_now": 0, "created": 0, "closed": 0,
                    "avg_rt_hours": 0.0, "p90_rt_hours": 0.0},
            "status_dist": {"labels": [], "counts": [], "palette": []},
            "bar": {"labels": [], "open_now": [], "created": [], "closed": []},
            "rows": []
        }

    # 專案狀態分布（圓餅）
    stat_rows = db.session.query(PDProject.project_status,
                                 db.func.count(PDProject.project_sid))\
        .filter(PDProject.status == 1)
    if statuses:
        stat_rows = stat_rows.filter(PDProject.project_status.in_(statuses))
    if kw:
        like = f"%{kw.strip()}%"
        stat_rows = stat_rows.filter(db.or_(PDProject.project_name.ilike(like),
                                            PDProject.project_no.ilike(like)))
    stat_rows = stat_rows.group_by(PDProject.project_status).all()
    st_labels = [(s or "未設定") for (s, _) in stat_rows]
    st_counts = [int(c) for (_, c) in stat_rows]

    # 區間內建立
    q_created = db.session.query(PDTicket.project_sid,
                                 db.func.count(PDTicket.ticket_sid))\
        .filter(PDTicket.status == 1,
                PDTicket.create_dt >= date_from,
                PDTicket.create_dt <= date_to)
    if proj_ids:
        q_created = q_created.filter(PDTicket.project_sid.in_(proj_ids))
    created_map = {pid: cnt for pid, cnt in
                   q_created.group_by(PDTicket.project_sid).all()}

    # 區間內完成（同時收集每張單的解決秒數）
    q_closed = db.session.query(PDTicket.project_sid,
                                PDTicket.ticket_status,
                                PDTicket.create_dt,
                                PDTicket.update_dt)\
        .filter(PDTicket.status == 1,
                PDTicket.update_dt >= date_from,
                PDTicket.update_dt <= date_to)
    if proj_ids:
        q_closed = q_closed.filter(PDTicket.project_sid.in_(proj_ids))
    closed_cnt_map: Dict[int, int] = {}
    secs_map: Dict[int, List[float]] = {}
    for pid, st, cdt, udt in q_closed.all():
        if not _is_done(st):
            continue
        if not (cdt and udt):
            continue
        sec = (udt - cdt).total_seconds()
        if sec < 0:
            continue
        closed_cnt_map[pid] = closed_cnt_map.get(pid, 0) + 1
        secs_map.setdefault(pid, []).append(sec)

    # 目前未結
    q_open_now = db.session.query(PDTicket.project_sid, PDTicket.ticket_status)\
        .filter(PDTicket.status == 1)
    if proj_ids:
        q_open_now = q_open_now.filter(PDTicket.project_sid.in_(proj_ids))
    open_now_map: Dict[int, int] = {}
    for pid, st in q_open_now.all():
        if _is_done(st):
            continue
        open_now_map[pid] = open_now_map.get(pid, 0) + 1

    # 百分位工具
    def _pctile(v: List[float], p: float) -> float:
        if not v:
            return 0.0
        s = sorted(v)
        k = (len(s)-1) * p
        f = int(k)
        c = min(f+1, len(s)-1)
        return s[f] if f == c else s[f] + (s[c]-s[f])*(k-f)

    # 表格 rows（★ 每專案補 avg 與 p90）
    rows: List[Dict[str, Any]] = []
    for p in projects:
        pid = p.project_sid
        secs = secs_map.get(pid, [])
        avg_rt = round((sum(secs)/len(secs))/3600.0, 2) if secs else 0.0
        p90_rt = round((_pctile(secs, 0.9))/3600.0, 2) if secs else 0.0
        rows.append({
            "project_sid": pid,
            "project_no": p.project_no,
            "project_name": p.project_name,
            "project_status": p.project_status or "",
            "start_dt": p.start_dt.strftime("%Y-%m-%d") if p.start_dt else "",
            "end_dt": p.end_dt.strftime("%Y-%m-%d") if p.end_dt else "",
            "created": int(created_map.get(pid, 0)),
            "closed": int(closed_cnt_map.get(pid, 0)),
            "open_now": int(open_now_map.get(pid, 0)),
            "avg_rt_hours": avg_rt,
            "p90_rt_hours": p90_rt,  # ★ 關鍵：供樣板 r.p90_rt_hours 使用
        })

    # 長條圖：open_now Top 10
    top = sorted(rows, key=lambda r: r["open_now"], reverse=True)[:10]
    bar_labels  = [r["project_name"] for r in top]
    bar_open    = [r["open_now"] for r in top]
    bar_created = [r["created"] for r in top]
    bar_closed  = [r["closed"] for r in top]

    # KPI：總覽（含全體 p90）
    kpi_projects = len(projects)
    kpi_open_now = sum(r["open_now"] for r in rows)
    kpi_created  = sum(r["created"] for r in rows)
    kpi_closed   = sum(r["closed"] for r in rows)

    all_secs_sum = sum(sum(secs_map.get(p.project_sid, [])) for p in projects)
    all_secs_cnt = sum(len(secs_map.get(p.project_sid, [])) for p in projects)
    kpi_avg_rt   = round((all_secs_sum/all_secs_cnt)/3600.0, 2) if all_secs_cnt else 0.0

    all_secs_all = [sec for v in secs_map.values() for sec in v]
    kpi_p90_rt   = round((_pctile(all_secs_all, 0.9))/3600.0, 2) if all_secs_all else 0.0

    return {
        "kpi": {
            "projects": kpi_projects,
            "open_now": kpi_open_now,
            "created": kpi_created,
            "closed": kpi_closed,
            "avg_rt_hours": kpi_avg_rt,
            "p90_rt_hours": kpi_p90_rt
        },
        "status_dist": {
            "labels": st_labels,
            "counts": st_counts,
            "palette": _palette(len(st_labels))
        },
        "bar": {
            "labels": bar_labels,
            "open_now": bar_open,
            "created": bar_created,
            "closed": bar_closed
        },
        "rows": rows
    }

@reports_ticket_bp.route("/projects/overview", methods=["GET"])
@login_required
def projects_overview_page():
    status_opts = [r[0] for r in db.session.query(PDProject.project_status)
                   .filter(PDProject.status == 1).distinct().order_by(PDProject.project_status.asc()).all() if r[0]]
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    date_from, date_to = _default_range_if_empty(date_from, date_to, days=90)
    statuses_param = request.args.get("status")
    statuses = [s for s in (statuses_param or "").split(",") if s.strip()] or None
    kw = request.args.get("kw", type=str)
    params = {"date_from": date_from, "date_to": date_to, "statuses": statuses, "kw": kw}
    data = _query_projects_overview(params)
    raw_params = {
        "date_from": date_from.strftime("%Y-%m-%d"),
        "date_to": date_to.strftime("%Y-%m-%d"),
        "status": ",".join(statuses) if statuses else "",
        "kw": kw or ""
    }
    return render_template("reports/projects_overview.html",
        ACTIVE_MENU="reports", ACTIVE_SUBMENU="projects", ACTIVE_ITEM="projects_overview",
        status_options=status_opts, params=raw_params, chart_data=data)

@reports_ticket_bp.route("/projects/overview/data", methods=["GET"])
@login_required
def projects_overview_data():
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    date_from, date_to = _default_range_if_empty(date_from, date_to, days=90)
    statuses_param = request.args.get("status")
    statuses = [s for s in (statuses_param or "").split(",") if s.strip()] or None
    kw = request.args.get("kw", type=str)
    params = {"date_from": date_from, "date_to": date_to, "statuses": statuses, "kw": kw}
    return jsonify(_query_projects_overview(params))

# ================================================================
# ====================== 專案進度追蹤（保留） ====================
# ================================================================
def _query_project_progress(params: Dict[str, Any]) -> Dict[str, Any]:
    """輸出：
      labels, series: [{name:'新增'},{name:'完成'},{name:'待辦(Backlog)'}]
      kpi: {created, closed, backlog_start, backlog_end, open_now, progress_pct}
      open_items: [最久未結前20]
      closed_items: [區間內已完成最新20]
    """
    project_sid: int | None = params.get("project_sid")
    if not project_sid:
        return {
            "labels": [], "series": [], "kpi": {"created":0,"closed":0,"backlog_start":0,"backlog_end":0,"open_now":0,"progress_pct":0.0},
            "open_items": [], "closed_items": []
        }

    date_from: datetime = params["date_from"]
    date_to: datetime = params["date_to"]
    interval: str = params.get("interval") or "day"

    # Buckets
    bucket_starts: List[date] = _iter_buckets(date_from, date_to, interval)
    labels = [_format_label(b, interval) for b in bucket_starts]
    idx = {b: i for i, b in enumerate(bucket_starts)}
    created_series = [0] * len(bucket_starts)
    closed_series = [0] * len(bucket_starts)

    # 區間新增
    q_new = db.session.query(PDTicket.create_dt).filter(
        PDTicket.status == 1, PDTicket.project_sid == project_sid,
        PDTicket.create_dt >= date_from, PDTicket.create_dt <= date_to
    )
    for (dt,) in q_new.all():
        key = _bucket_key(dt, interval)
        if key in idx: created_series[idx[key]] += 1

    # 區間完成
    q_closed = db.session.query(PDTicket.update_dt, PDTicket.ticket_status).filter(
        PDTicket.status == 1, PDTicket.project_sid == project_sid,
        PDTicket.update_dt >= date_from, PDTicket.update_dt <= date_to
    )
    for udt, st in q_closed.all():
        if not _is_done(st): continue
        key = _bucket_key(udt, interval)
        if key in idx: closed_series[idx[key]] += 1

    # 起始 Backlog（date_from 之前開啟、且尚未在 date_from 前就關閉）
    q_pre = db.session.query(PDTicket.create_dt, PDTicket.update_dt, PDTicket.ticket_status).filter(
        PDTicket.status == 1, PDTicket.project_sid == project_sid, PDTicket.create_dt < date_from
    )
    backlog_start = 0
    for cdt, udt, st in q_pre.all():
        if _is_done(st) and udt and udt < date_from:
            continue  # 已在期間開始前結束
        backlog_start += 1

    # Backlog 累加
    backlog_series = []
    running = backlog_start
    for i in range(len(bucket_starts)):
        running += created_series[i] - closed_series[i]
        backlog_series.append(running)

    backlog_end = running

    # KPI：目前未結
    open_now = db.session.query(db.func.count(PDTicket.ticket_sid))\
        .filter(PDTicket.status == 1, PDTicket.project_sid == project_sid)\
        .filter(~db.func.lower(PDTicket.ticket_status).in_(_DONE_KEYWORDS_LOWER)).scalar() or 0

    # 進度百分比（截至 date_to）：已關閉 / 總 ticket（create_dt <= date_to）
    total_exist = db.session.query(db.func.count(PDTicket.ticket_sid))\
        .filter(PDTicket.status == 1, PDTicket.project_sid == project_sid, PDTicket.create_dt <= date_to).scalar() or 0
    closed_up_to = db.session.query(db.func.count(PDTicket.ticket_sid))\
        .filter(PDTicket.status == 1, PDTicket.project_sid == project_sid,
                PDTicket.update_dt <= date_to).filter(
                    db.or_(*[db.func.lower(PDTicket.ticket_status).like(f"%{k}%") for k in _DONE_KEYWORDS_LOWER])
                ).scalar() or 0
    progress_pct = round((closed_up_to/total_exist*100.0), 1) if total_exist else 0.0

    # Top 未結（最久）
    now = datetime.utcnow()
    open_rows = db.session.query(
        PDTicket.ticket_sid, PDTicket.ticket_no, PDTicket.ticket_name, PDTicket.create_dt
    ).filter(
        PDTicket.status == 1, PDTicket.project_sid == project_sid
    ).filter(
        ~db.func.lower(PDTicket.ticket_status).in_(_DONE_KEYWORDS_LOWER)
    ).order_by(PDTicket.create_dt.asc()).limit(20).all()
    open_items = [{
        "ticket_sid": r.ticket_sid, "ticket_no": r.ticket_no, "ticket_name": r.ticket_name,
        "created_at": r.create_dt.strftime("%Y-%m-%d %H:%M"),
        "age_hours": round(((now - r.create_dt).total_seconds())/3600.0, 1)
    } for r in open_rows]

    # 區間內已完成（最新）
    closed_rows = db.session.query(
        PDTicket.ticket_sid, PDTicket.ticket_no, PDTicket.ticket_name,
        PDTicket.create_dt, PDTicket.update_dt, PDTicket.ticket_status
    ).filter(
        PDTicket.status == 1, PDTicket.project_sid == project_sid,
        PDTicket.update_dt >= date_from, PDTicket.update_dt <= date_to
    ).order_by(PDTicket.update_dt.desc()).limit(20).all()
    closed_items = []
    for r in closed_rows:
        if not _is_done(r.ticket_status): continue
        if not (r.create_dt and r.update_dt): continue
        hrs = (r.update_dt - r.create_dt).total_seconds()/3600.0
        closed_items.append({
            "ticket_sid": r.ticket_sid, "ticket_no": r.ticket_no, "ticket_name": r.ticket_name,
            "created_at": r.create_dt.strftime("%Y-%m-%d %H:%M"),
            "closed_at": r.update_dt.strftime("%Y-%m-%d %H:%M"),
            "lead_hours": round(hrs, 2)
        })

    return {
        "labels": labels,
        "series": [
            {"name": "新增", "data": created_series},
            {"name": "完成", "data": closed_series},
            {"name": "待辦(Backlog)", "data": backlog_series},
        ],
        "kpi": {
            "created": sum(created_series),
            "closed": sum(closed_series),
            "backlog_start": backlog_start,
            "backlog_end": backlog_end,
            "open_now": int(open_now),
            "progress_pct": progress_pct
        },
        "open_items": open_items,
        "closed_items": closed_items
    }

@reports_ticket_bp.route("/projects/progress", methods=["GET"])
@login_required
def project_progress_page():
    # 專案清單
    projects = PDProject.query.filter(PDProject.status == 1).order_by(PDProject.project_name.asc()).all()
    # 預設選第一個專案（若沒帶 project_sid）
    default_pid = projects[0].project_sid if projects else None

    project_sid = request.args.get("project_sid", type=int) or default_pid
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    date_from, date_to = _default_range_if_empty(date_from, date_to, days=60)
    interval = request.args.get("interval", "day")
    if interval not in ("day", "week", "month"): interval = "day"

    params = {"project_sid": project_sid, "date_from": date_from, "date_to": date_to, "interval": interval}
    data = _query_project_progress(params)

    raw_params = {
        "project_sid": project_sid or "",
        "date_from": date_from.strftime("%Y-%m-%d"),
        "date_to": date_to.strftime("%Y-%m-%d"),
        "interval": interval
    }
    return render_template(
        "reports/project_progress.html",
        ACTIVE_MENU="reports",
        ACTIVE_SUBMENU="projects",
        ACTIVE_ITEM="project_progress",
        projects=projects,
        params=raw_params,
        chart_data=data
    )

@reports_ticket_bp.route("/projects/progress/data", methods=["GET"])
@login_required
def project_progress_data():
    project_sid = request.args.get("project_sid", type=int)
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    date_from, date_to = _default_range_if_empty(date_from, date_to, days=60)
    interval = request.args.get("interval", "day")
    if interval not in ("day", "week", "month"): interval = "day"
    params = {"project_sid": project_sid, "date_from": date_from, "date_to": date_to, "interval": interval}
    return jsonify(_query_project_progress(params))

# ================================================================
# ===================== 專案分類分布（新增） =====================
# ================================================================
def _query_project_category(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    回傳：
    {
      "pie":    {"labels":[], "counts":[], "palette":[]},
      "stack":  {"labels":[], "open":[], "closed":[]},
      "kpi":    {"total":N, "categories":M, "top":{"label":..., "count":..., "pct":...}, "closed_pct":xx.x},
      "rows":   [{"type_sid":..., "type_name":"...", "count":..., "percent":..., "open":..., "closed":...}]
    }
    定義：
      - 樣本為「在 [date_from, date_to] 期間內建立的工單」
      - closed 計算條件：status 符合完成關鍵字，且 update_dt <= date_to（即該期間結束時視為已完成）
      - open = count - closed
    """
    project_sid: int | None = params.get("project_sid")
    date_from: datetime = params["date_from"]
    date_to: datetime = params["date_to"]
    type_group: str | None = params.get("type_group")

    # 基礎查詢（期間內建立）
    base = db.session.query(
        PDTicket.ticket_type,
        db.func.count(PDTicket.ticket_sid).label("cnt")
    ).outerjoin(PDType, PDTicket.ticket_type == PDType.type_sid)\
     .filter(PDTicket.status == 1, PDTicket.create_dt >= date_from, PDTicket.create_dt <= date_to)

    if project_sid:
        base = base.filter(PDTicket.project_sid == project_sid)
    if type_group:
        base = base.filter(PDType.type_group == type_group)

    base = base.group_by(PDTicket.ticket_type)
    base_rows = base.all()  # [(type_sid, cnt), ...]

    if not base_rows:
        return {
            "pie": {"labels": [], "counts": [], "palette": []},
            "stack": {"labels": [], "open": [], "closed": []},
            "kpi": {"total": 0, "categories": 0, "top": {"label": "", "count": 0, "pct": 0.0}, "closed_pct": 0.0},
            "rows": []
        }

    # 期間內完成（樣本同上）
    closed_q = db.session.query(
        PDTicket.ticket_type,
        db.func.count(PDTicket.ticket_sid).label("cnt")
    ).outerjoin(PDType, PDTicket.ticket_type == PDType.type_sid)\
     .filter(PDTicket.status == 1,
             PDTicket.create_dt >= date_from, PDTicket.create_dt <= date_to,
             PDTicket.update_dt <= date_to,
             _done_sql_expr())
    if project_sid:
        closed_q = closed_q.filter(PDTicket.project_sid == project_sid)
    if type_group:
        closed_q = closed_q.filter(PDType.type_group == type_group)
    closed_q = closed_q.group_by(PDTicket.ticket_type)
    closed_rows = closed_q.all()  # [(type_sid, cnt_closed), ...]

    # 蒐集名稱
    sids = [sid for sid, _ in base_rows if sid is not None]
    name_map: Dict[int, str] = {}
    if sids:
        for sid, name in db.session.query(PDType.type_sid, PDType.type_name)\
                                   .filter(PDType.type_sid.in_(sids)).all():
            name_map[sid] = name

    total = int(sum(int(c) for _, c in base_rows))
    counts_map = {sid: int(c) for sid, c in base_rows}
    closed_map = {sid: int(c) for sid, c in closed_rows}
    all_keys = list(counts_map.keys())
    # 排序（由大到小）
    all_keys.sort(key=lambda k: counts_map.get(k, 0), reverse=True)

    rows: List[Dict[str, Any]] = []
    labels: List[str] = []
    counts: List[int] = []
    opens: List[int] = []
    closeds: List[int] = []

    top_label, top_count = "", 0

    for sid in all_keys:
        name = name_map.get(sid, "未分類")
        cnt = counts_map.get(sid, 0)
        cls = closed_map.get(sid, 0)
        opn = max(cnt - cls, 0)

        pct = round((cnt/total*100.0), 2) if total else 0.0
        rows.append({
            "type_sid": sid if sid is not None else None,
            "type_name": name,
            "count": cnt,
            "percent": pct,
            "open": opn,
            "closed": cls
        })

        labels.append(name); counts.append(cnt); opens.append(opn); closeds.append(cls)

        if cnt > top_count:
            top_label, top_count = name, cnt

    closed_total = sum(closeds)
    closed_pct = round((closed_total/total*100.0), 1) if total else 0.0

    return {
        "pie": {"labels": labels, "counts": counts, "palette": _palette(len(labels))},
        "stack": {"labels": labels, "open": opens, "closed": closeds},
        "kpi": {
            "total": total,
            "categories": len(all_keys),
            "top": {"label": top_label, "count": top_count, "pct": round((top_count/total*100.0), 1) if total else 0.0},
            "closed_pct": closed_pct
        },
        "rows": rows
    }

@reports_ticket_bp.route("/projects/category", methods=["GET"])
@login_required
def project_category_page():
    # 專案清單
    projects = PDProject.query.filter(PDProject.status == 1).order_by(PDProject.project_name.asc()).all()
    default_pid = projects[0].project_sid if projects else None
    project_sid = request.args.get("project_sid", type=int) or default_pid

    # 類別群組選單
    type_groups = [r[0] for r in db.session.query(PDType.type_group)
                   .filter(PDType.status == 1)
                   .distinct().order_by(PDType.type_group.asc()).all() if r[0]]

    # 日期
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    date_from, date_to = _default_range_if_empty(date_from, date_to, days=60)

    # 類別群組過濾
    type_group = request.args.get("type_group", type=str) or None

    params = {"project_sid": project_sid, "date_from": date_from, "date_to": date_to, "type_group": type_group}
    data = _query_project_category(params)

    raw_params = {
        "project_sid": project_sid or "",
        "date_from": date_from.strftime("%Y-%m-%d"),
        "date_to": date_to.strftime("%Y-%m-%d"),
        "type_group": type_group or ""
    }
    return render_template("reports/project_category.html",
        ACTIVE_MENU="reports", ACTIVE_SUBMENU="projects", ACTIVE_ITEM="project_category",
        projects=projects, type_groups=type_groups, params=raw_params, chart_data=data)

@reports_ticket_bp.route("/projects/category/data", methods=["GET"])
@login_required
def project_category_data():
    project_sid = request.args.get("project_sid", type=int)
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    date_from, date_to = _default_range_if_empty(date_from, date_to, days=60)
    type_group = request.args.get("type_group", type=str) or None

    params = {"project_sid": project_sid, "date_from": date_from, "date_to": date_to, "type_group": type_group}
    return jsonify(_query_project_category(params))
