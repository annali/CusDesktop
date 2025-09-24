# backend/app/views/reports_sla_overdue.py
from __future__ import annotations
from datetime import datetime, timedelta, date
from typing import Dict, Any, List, Tuple

from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import login_required

from ..models import db, PDTicket, PDProject

# 請注意：Blueprint 變數名稱是 reports_sla_overdue_bp
reports_sla_overdue_bp = Blueprint("reports_sla_overdue", __name__, url_prefix="/reports/sla")

# ------------------------ 工具：日期處理 ------------------------
def _parse_date(s: str | None, end: bool = False):
    if not s:
        return None
    try:
        d = datetime.strptime(s, "%Y-%m-%d")
        return d.replace(hour=23, minute=59, second=59, microsecond=999999) if end \
             else d.replace(hour=0, minute=0, second=0, microsecond=0)
    except Exception:
        return None

def _default_range_if_empty(date_from: datetime | None, date_to: datetime | None, days: int = 30) -> Tuple[datetime, datetime]:
    """若未帶日期，預設近 N 天（含當日）"""
    if not date_to:
        now = datetime.utcnow()
        date_to = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    if not date_from:
        date_from = (date_to - timedelta(days=days-1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return date_from, date_to

# ------------------------ 完成關鍵字（與既有一致） ------------------------
_DONE_WORDS = [
    "done","closed","resolve","resolved","finish","finished","complete","completed",
    "完成","已完成","結案","已結案","關閉","已關閉","已處理","已解決"
]
_DONE_WORDS_L = [w.lower() for w in _DONE_WORDS]
def _is_done(status: str | None) -> bool:
    s = (status or "").lower()
    return any(k in s for k in _DONE_WORDS_L)

# ------------------------ Buckets（趨勢分組） ------------------------
def _iter_buckets(date_from: datetime, date_to: datetime, interval: str) -> List[date]:
    out: List[date] = []
    if interval == "month":
        cur = date(date_from.year, date_from.month, 1)
        end = date(date_to.year, date_to.month, 1)
        while cur <= end:
            out.append(cur)
            cur = date(cur.year + (1 if cur.month == 12 else 0),
                       1 if cur.month == 12 else cur.month + 1, 1)
    elif interval == "week":
        start = (date_from.date() - timedelta(days=date_from.weekday()))
        end = (date_to.date() - timedelta(days=date_to.weekday()))
        cur = start
        while cur <= end:
            out.append(cur); cur = cur + timedelta(days=7)
    else:
        cur = date_from.date(); end = date_to.date()
        while cur <= end:
            out.append(cur); cur = cur + timedelta(days=1)
    return out

def _bucket_key(dt: datetime, interval: str) -> date:
    if interval == "month": return date(dt.year, dt.month, 1)
    if interval == "week":
        d = dt.date(); return d - timedelta(days=d.weekday())
    return dt.date()

def _format_label(d: date, interval: str) -> str:
    if interval == "month": return d.strftime("%Y-%m")
    if interval == "week":
        iso = d.isocalendar()
        return f"{d.strftime('%Y-%m-%d')} (W{iso.week:02d})"
    return d.strftime("%Y-%m-%d")

# ------------------------ SLA 規則 ------------------------
SLA_RULES_DEFAULT = {
    "p1": 4, "critical": 4, "urgent": 4,
    "p2": 8, "high": 8,
    "p3": 24, "medium": 24,
    "p4": 72, "low": 72,
    "__default__": 24
}
def _norm_pri(s: str | None) -> str:
    return (s or "").strip().lower()

def _compose_rules(params: Dict[str, Any]) -> Dict[str, int]:
    rules = SLA_RULES_DEFAULT.copy()
    for key, norm in (("p1h","p1"),("p2h","p2"),("p3h","p3"),("p4h","p4"),("defh","__default__")):
        v = params.get(key)
        if v is not None:
            try:
                rules[norm] = max(0, int(v))
            except Exception:
                pass
    return rules

def _target_hours(priority: str | None, rules: Dict[str,int]) -> int:
    p = _norm_pri(priority)
    if p in rules: return rules[p]
    alias = {"critical":"p1","urgent":"p1","high":"p2","medium":"p3","normal":"p3","standard":"p3","low":"p4"}
    if p in alias and alias[p] in rules:
        return rules[alias[p]]
    return rules["__default__"]

# ------------------------ 主查詢：逾期客服單 ------------------------
def _query_overdue(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    樣本：期間內（create_dt ∈ [date_from, date_to]）建立的客服單。
    逾期判定：
      - 已結： (update_dt - create_dt) > 目標 SLA 小時
      - 未結： (現在 - create_dt) > 目標 SLA 小時
    結果包含 KPI、依優先級分布、趨勢（開/關逾期）、以及逾期 Top 樣本。
    """
    project_sid: int | None = params.get("project_sid")
    date_from: datetime = params["date_from"]
    date_to: datetime = params["date_to"]
    interval: str = params.get("interval") or "day"
    rules = _compose_rules(params)

    # 樣本：期間內新建
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

    # 趨勢 bucket
    buckets = _iter_buckets(date_from, date_to, interval)
    labels = [_format_label(b, interval) for b in buckets]
    idx = {b: i for i, b in enumerate(buckets)}
    trend_open_bre = [0]*len(buckets)
    trend_closed_bre = [0]*len(buckets)

    # 依優先級
    pr_list: List[str] = []
    per_pr_open: Dict[str, int] = {}
    per_pr_closed: Dict[str, int] = {}

    # KPI 與樣本
    overdue_open = 0
    overdue_closed = 0
    samples: List[Dict[str, Any]] = []

    now = datetime.utcnow()

    for r in rows:
        tgt_h = _target_hours(r.ticket_priority, rules)
        pr = _norm_pri(r.ticket_priority) or "未設定"
        if pr not in pr_list: pr_list.append(pr)

        if _is_done(r.ticket_status) and r.update_dt:
            tat_h = max(0.0, (r.update_dt - r.create_dt).total_seconds()/3600.0) if r.create_dt else 0.0
            if tat_h > tgt_h:
                overdue_closed += 1
                per_pr_closed[pr] = per_pr_closed.get(pr, 0) + 1
                b = _bucket_key(r.update_dt, interval)
                if b in idx: trend_closed_bre[idx[b]] += 1
                # 樣本
                samples.append({
                    "ticket_no": r.ticket_no,
                    "ticket_name": r.ticket_name,
                    "priority": r.ticket_priority or "",
                    "status": r.ticket_status or "",
                    "created_at": r.create_dt.strftime("%Y-%m-%d %H:%M") if r.create_dt else "",
                    "resolved_at": r.update_dt.strftime("%Y-%m-%d %H:%M"),
                    "target_h": tgt_h,
                    "tat_h": round(tat_h, 2),
                    "overdue_h": round(tat_h - tgt_h, 2)
                })
        else:
            age_h = max(0.0, (now - r.create_dt).total_seconds()/3600.0) if r.create_dt else 0.0
            if age_h > tgt_h:
                overdue_open += 1
                per_pr_open[pr] = per_pr_open.get(pr, 0) + 1
                b = _bucket_key(r.create_dt, interval)
                if b in idx: trend_open_bre[idx[b]] += 1
                # 樣本
                samples.append({
                    "ticket_no": r.ticket_no,
                    "ticket_name": r.ticket_name,
                    "priority": r.ticket_priority or "",
                    "status": r.ticket_status or "",
                    "created_at": r.create_dt.strftime("%Y-%m-%d %H:%M") if r.create_dt else "",
                    "resolved_at": "",
                    "target_h": tgt_h,
                    "tat_h": round(age_h, 2),
                    "overdue_h": round(age_h - tgt_h, 2)
                })

    # 樣本 Top 50（依逾期時數降冪）
    samples.sort(key=lambda s: s["overdue_h"], reverse=True)
    top_samples = samples[:50]

    # 優先級排序（總逾期量）
    def _total(pr: str) -> int:
        return per_pr_open.get(pr, 0) + per_pr_closed.get(pr, 0)
    pr_list.sort(key=_total, reverse=True)

    bar = {
        "labels": pr_list,
        "open_breached": [per_pr_open.get(p, 0) for p in pr_list],
        "closed_breached": [per_pr_closed.get(p, 0) for p in pr_list]
    }

    kpi = {
        "overdue_total": overdue_open + overdue_closed,
        "overdue_open": overdue_open,
        "overdue_closed": overdue_closed
    }

    trend = {
        "labels": labels,
        "open_breached": trend_open_bre,
        "closed_breached": trend_closed_bre,
        "total": [trend_open_bre[i] + trend_closed_bre[i] for i in range(len(labels))]
    }

    return {
        "kpi": kpi,
        "bar": bar,
        "trend": trend,
        "samples": top_samples,
        "rules": {
            "by_priority_hours": {
                "p1": rules.get("p1", SLA_RULES_DEFAULT["p1"]),
                "p2": rules.get("p2", SLA_RULES_DEFAULT["p2"]),
                "p3": rules.get("p3", SLA_RULES_DEFAULT["p3"]),
                "p4": rules.get("p4", SLA_RULES_DEFAULT["p4"]),
                "__default__": rules.get("__default__", SLA_RULES_DEFAULT["__default__"])
            }
        }
    }

# ------------------------ 頁面與 API ------------------------
@reports_sla_overdue_bp.route("/overdue", methods=["GET"])
@login_required
def overdue_page():
    # 專案清單
    projects = PDProject.query.filter(PDProject.status == 1)\
        .order_by(PDProject.project_name.asc()).all()
    default_pid = projects[0].project_sid if projects else None

    project_sid = request.args.get("project_sid", type=int) or default_pid
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    date_from, date_to = _default_range_if_empty(date_from, date_to, days=30)
    interval = request.args.get("interval", "day")
    if interval not in ("day","week","month"): interval = "day"

    p1h = request.args.get("p1h", type=int)
    p2h = request.args.get("p2h", type=int)
    p3h = request.args.get("p3h", type=int)
    p4h = request.args.get("p4h", type=int)
    defh = request.args.get("defh", type=int)

    params = {
        "project_sid": project_sid, "date_from": date_from, "date_to": date_to, "interval": interval,
        "p1h": p1h, "p2h": p2h, "p3h": p3h, "p4h": p4h, "defh": defh
    }
    data = _query_overdue(params)

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
        "reports/sla_overdue.html",
        ACTIVE_MENU="reports", ACTIVE_SUBMENU="sla", ACTIVE_ITEM="sla_overdue",
        projects=projects, params=raw_params, chart_data=data
    )

@reports_sla_overdue_bp.route("/overdue/data", methods=["GET"])
@login_required
def overdue_data():
    project_sid = request.args.get("project_sid", type=int)
    date_from = _parse_date(request.args.get("date_from"))
    date_to = _parse_date(request.args.get("date_to"), end=True)
    date_from, date_to = _default_range_if_empty(date_from, date_to, days=30)
    interval = request.args.get("interval", "day")
    if interval not in ("day","week","month"): interval = "day"

    p1h = request.args.get("p1h", type=int)
    p2h = request.args.get("p2h", type=int)
    p3h = request.args.get("p3h", type=int)
    p4h = request.args.get("p4h", type=int)
    defh = request.args.get("defh", type=int)

    params = {
        "project_sid": project_sid, "date_from": date_from, "date_to": date_to, "interval": interval,
        "p1h": p1h, "p2h": p2h, "p3h": p3h, "p4h": p4h, "defh": defh
    }
    return jsonify(_query_overdue(params))

# 友善尾斜線
@reports_sla_overdue_bp.route("/overdue/", methods=["GET"])
@login_required
def overdue_page_trailing():
    return redirect(url_for("reports_sla_overdue.overdue_page", **request.args))

@reports_sla_overdue_bp.route("/overdue/data/", methods=["GET"])
@login_required
def overdue_data_trailing():
    return redirect(url_for("reports_sla_overdue.overdue_data", **request.args))
