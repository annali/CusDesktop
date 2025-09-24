from __future__ import annotations
from datetime import datetime, timedelta, date
from typing import Dict, Any, List, Tuple

from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import login_required

from ..models import db, PDTicket, PDProject

# 本藍圖
reports_sla_bp = Blueprint("reports_sla", __name__, url_prefix="/reports")

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

# ------------------------ 完成關鍵字（與既有一致） ------------------------
DONE_KEYWORDS = [
    "done", "closed", "resolve", "resolved", "finish", "finished", "complete", "completed",
    "完成", "已完成", "結案", "已結案", "關閉", "已關閉", "已處理", "已解決"
]
_DONE_KEYWORDS_LOWER = [k.lower() for k in DONE_KEYWORDS]

def _is_done(status: str | None) -> bool:
    s = (status or "").lower()
    return any(k in s for k in _DONE_KEYWORDS_LOWER)

# ------------------------ 時間區間 bucket ------------------------
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

def _palette(n: int) -> List[str]:
    return [f"hsl({(i*37)%360} 60% 52%)" for i in range(max(n,0))]

# ------------------------ SLA 規則（預設） ------------------------
SLA_RULES_DEFAULT = {
    "p1": 4, "critical": 4, "urgent": 4,            # 小時
    "p2": 8, "high": 8,
    "p3": 24, "medium": 24,
    "p4": 72, "low": 72,
    "__default__": 24
}

def _normalize_priority(s: str | None) -> str:
    return (s or "").strip().lower()

def _compose_rules_from_params(params: Dict[str, Any]) -> Dict[str, int]:
    rules = SLA_RULES_DEFAULT.copy()
    # 支援 query 覆蓋（例如 ?p1h=2&p2h=6）
    for key, norm in (("p1h","p1"),("p2h","p2"),("p3h","p3"),("p4h","p4"),("defh","__default__")):
        v = params.get(key)
        if v is not None:
            try:
                rules[norm] = max(0, int(v))
            except Exception:
                pass
    return rules

# ------------------------ 主查詢：SLA 達成率 ------------------------
def _query_sla_achievement(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    樣本：期間內（create_dt ∈ [date_from, date_to]）建立的客服單。
    SLA 規則：依 ticket_priority（大小寫不敏感）套用 SLA hours（可透過 query 覆蓋）。
    達成判定：
      - 關閉單： (update_dt - create_dt) 小時 <= 目標小時 → 達成
      - 未關閉： 目前 age 小時 <= 目標小時 → 暫列「未逾期」，否則「逾期中」
    回傳包含 KPI、圓餅、分優先級長條、趨勢與逾期樣本。
    """
    project_sid: int | None = params.get("project_sid")
    date_from: datetime = params["date_from"]
    date_to: datetime = params["date_to"]
    interval: str = params.get("interval") or "day"
    rules = _compose_rules_from_params(params)

    # 取樣本（期間內新建）
    q = db.session.query(
        PDTicket.ticket_sid, PDTicket.ticket_no, PDTicket.ticket_name,
        PDTicket.ticket_priority, PDTicket.ticket_status,
        PDTicket.create_dt, PDTicket.update_dt
    ).filter(
        PDTicket.status == 1,
        PDTicket.create_dt >= date_from,
        PDTicket.create_dt <= date_to
    )
    if project_sid:
        q = q.filter(PDTicket.project_sid == project_sid)

    rows = q.order_by(PDTicket.create_dt.asc()).all()
    total = len(rows)

    # Trend buckets
    bucket_starts: List[date] = _iter_buckets(date_from, date_to, interval)
    labels = [_format_label(b, interval) for b in bucket_starts]
    idx = {b: i for i, b in enumerate(bucket_starts)}
    closed_ach_series = [0]*len(bucket_starts)
    closed_bre_series = [0]*len(bucket_starts)

    # Priority 分布
    pr_keys: List[str] = []
    per_pr_ach: Dict[str, int] = {}
    per_pr_bre: Dict[str, int] = {}

    # KPI counters
    closed = 0
    achieved_closed = 0
    breached_closed = 0
    open_ = 0
    open_within = 0
    open_breach = 0

    # 逾期樣本
    samples: List[Dict[str, Any]] = []

    now = datetime.utcnow()

    def _target_hours(priority: str | None) -> int:
        p = _normalize_priority(priority)
        if p in rules: return rules[p]
        alias = {"critical":"p1","urgent":"p1","high":"p2","medium":"p3","normal":"p3","standard":"p3","low":"p4"}
        if p in alias and alias[p] in rules:
            return rules[alias[p]]
        return rules["__default__"]

    for r in rows:
        pr = _normalize_priority(r.ticket_priority) or "未設定"
        if pr not in pr_keys:
            pr_keys.append(pr)
        tgt = _target_hours(r.ticket_priority)

        if _is_done(r.ticket_status) and r.update_dt:
            closed += 1
            tat_hours = max(0.0, (r.update_dt - r.create_dt).total_seconds()/3600.0) if r.create_dt else 0.0
            bucket = _bucket_key(r.update_dt, interval)
            if tat_hours <= tgt:
                achieved_closed += 1
                if bucket in idx: closed_ach_series[idx[bucket]] += 1
                per_pr_ach[pr] = per_pr_ach.get(pr, 0) + 1
            else:
                breached_closed += 1
                if bucket in idx: closed_bre_series[idx[bucket]] += 1
                per_pr_bre[pr] = per_pr_bre.get(pr, 0) + 1
                overdue = tat_hours - tgt
                samples.append({
                    "ticket_no": r.ticket_no, "ticket_name": r.ticket_name, "priority": r.ticket_priority or "",
                    "created_at": r.create_dt.strftime("%Y-%m-%d %H:%M") if r.create_dt else "",
                    "resolved_at": r.update_dt.strftime("%Y-%m-%d %H:%M"),
                    "target_h": tgt, "tat_h": round(tat_hours, 2), "overdue_h": round(overdue, 2)
                })
        else:
            open_ += 1
            age_hours = max(0.0, (now - r.create_dt).total_seconds()/3600.0) if r.create_dt else 0.0
            if age_hours <= tgt:
                open_within += 1
            else:
                open_breach += 1
                overdue = age_hours - tgt
                samples.append({
                    "ticket_no": r.ticket_no, "ticket_name": r.ticket_name, "priority": r.ticket_priority or "",
                    "created_at": r.create_dt.strftime("%Y-%m-%d %H:%M") if r.create_dt else "",
                    "resolved_at": "", "target_h": tgt, "tat_h": round(age_hours, 2), "overdue_h": round(overdue, 2)
                })

    samples.sort(key=lambda s: s["overdue_h"], reverse=True)
    breached_samples = samples[:50]

    def _total_for_pr(pr: str) -> int:
        return per_pr_ach.get(pr, 0) + per_pr_bre.get(pr, 0)
    pr_keys.sort(key=_total_for_pr, reverse=True)
    bar_labels = pr_keys
    bar_ach = [per_pr_ach.get(pr, 0) for pr in pr_keys]
    bar_bre = [per_pr_bre.get(pr, 0) for pr in pr_keys]

    kpi = {
        "total": total,
        "closed": closed,
        "open": open_,
        "achieved_closed": achieved_closed,
        "breached_closed": breached_closed,
        "achieve_rate_closed": round((achieved_closed/closed*100.0), 1) if closed else 0.0,
        "achieved_all_now": achieved_closed + open_within,
        "achieve_rate_all_now": round(((achieved_closed + open_within)/total*100.0), 1) if total else 0.0,
        "open_within_sla": open_within,
        "open_breach": open_breach
    }

    pie = {"labels": ["達成(已結)", "逾期(已結)"], "counts": [achieved_closed, breached_closed], "palette": _palette(2)}

    rate_series = []
    for i in range(len(bucket_starts)):
        a = closed_ach_series[i]; b = closed_bre_series[i]; c = a + b
        rate_series.append(round((a/c*100.0), 1) if c else 0.0)

    trend = {"labels": labels, "closed_achieved": closed_ach_series, "closed_breached": closed_bre_series, "rate": rate_series}

    return {
        "kpi": kpi,
        "pie": pie,
        "bar_by_priority": {"labels": bar_labels, "achieved": bar_ach, "breached": bar_bre},
        "trend": trend,
        "breached_samples": breached_samples,
        "rules": {"by_priority_hours": {
            "p1": rules.get("p1", SLA_RULES_DEFAULT["p1"]),
            "p2": rules.get("p2", SLA_RULES_DEFAULT["p2"]),
            "p3": rules.get("p3", SLA_RULES_DEFAULT["p3"]),
            "p4": rules.get("p4", SLA_RULES_DEFAULT["p4"]),
            "__default__": rules.get("__default__", SLA_RULES_DEFAULT["__default__"])
        }}
    }

# ------------------------ 頁面與資料 API ------------------------
@reports_sla_bp.route("/sla/achievement", methods=["GET"])
@login_required
def sla_achievement_page():
    # 專案清單
    projects = PDProject.query.filter(PDProject.status == 1).order_by(PDProject.project_name.asc()).all()
    default_pid = projects[0].project_sid if projects else None

    project_sid = request.args.get("project_sid", type=int) or default_pid
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    date_from, date_to = _default_range_if_empty(date_from, date_to, days=30)
    interval = request.args.get("interval", "day")
    if interval not in ("day", "week", "month"): interval = "day"

    # SLA 覆寫參數
    p1h = request.args.get("p1h", type=int)
    p2h = request.args.get("p2h", type=int)
    p3h = request.args.get("p3h", type=int)
    p4h = request.args.get("p4h", type=int)
    defh = request.args.get("defh", type=int)

    params = {
        "project_sid": project_sid, "date_from": date_from, "date_to": date_to, "interval": interval,
        "p1h": p1h, "p2h": p2h, "p3h": p3h, "p4h": p4h, "defh": defh
    }
    data = _query_sla_achievement(params)

    raw_params = {
        "project_sid": project_sid or "",
        "date_from": date_from.strftime("%Y-%m-%d"),
        "date_to": date_to.strftime("%Y-%m-%d"),
        "interval": interval,
        "p1h": p1h if p1h is not None else "",
        "p2h": p2h if p2h is not None else "",
        "p3h": p3h if p3h is not None else "",
        "p4h": p4h if p4h is not None else "",
        "defh": defh if defh is not None else ""
    }
    return render_template(
        "reports/sla_achievement.html",
        ACTIVE_MENU="reports",
        ACTIVE_SUBMENU="sla",
        ACTIVE_ITEM="sla_rate",  # 與側欄判斷一致
        projects=projects,
        params=raw_params,
        chart_data=data
    )

@reports_sla_bp.route("/sla/achievement/data", methods=["GET"])
@login_required
def sla_achievement_data():
    project_sid = request.args.get("project_sid", type=int)
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    date_from, date_to = _default_range_if_empty(date_from, date_to, days=30)
    interval = request.args.get("interval", "day")
    if interval not in ("day", "week", "month"): interval = "day"

    p1h = request.args.get("p1h", type=int)
    p2h = request.args.get("p2h", type=int)
    p3h = request.args.get("p3h", type=int)
    p4h = request.args.get("p4h", type=int)
    defh = request.args.get("defh", type=int)

    params = {
        "project_sid": project_sid, "date_from": date_from, "date_to": date_to, "interval": interval,
        "p1h": p1h, "p2h": p2h, "p3h": p3h, "p4h": p4h, "defh": defh
    }
    return jsonify(_query_sla_achievement(params))

# ------------------------ 友善 alias：對應側欄 /reports/sla/rate ------------------------
@reports_sla_bp.route("/sla/rate", methods=["GET"])
@login_required
def sla_rate_page():
    # 直接使用同一份頁面邏輯
    return sla_achievement_page()

@reports_sla_bp.route("/sla/rate/", methods=["GET"])
@login_required
def sla_rate_page_slash():
    return sla_achievement_page()

@reports_sla_bp.route("/sla/rate/data", methods=["GET"])
@login_required
def sla_rate_data():
    return sla_achievement_data()

@reports_sla_bp.route("/sla/rate/data/", methods=["GET"])
@login_required
def sla_rate_data_slash():
    return sla_achievement_data()

# 其它 alias / redirect，避免 404
@reports_sla_bp.route("/sla", methods=["GET"])
@login_required
def sla_index_redirect():
    return redirect(url_for("reports_sla.sla_rate_page"))

@reports_sla_bp.route("/sla/achievement/", methods=["GET"])
@login_required
def sla_achievement_page_trailing_slash():
    return redirect(url_for("reports_sla.sla_achievement_page", **request.args))

@reports_sla_bp.route("/sla/achievement/data/", methods=["GET"])
@login_required
def sla_achievement_data_trailing_slash():
    return redirect(url_for("reports_sla.sla_achievement_data", **request.args))

# 直接訪問 /reports 或 /reports/ → 導向 SLA 達成率頁
@reports_sla_bp.route("", methods=["GET"])
@login_required
def reports_root_redirect():
    return redirect(url_for("reports_sla.sla_rate_page"))

@reports_sla_bp.route("/", methods=["GET"])
@login_required
def reports_root_redirect_slash():
    return redirect(url_for("reports_sla.sla_rate_page"))
