# backend/app/views/reports_satisfaction.py
from flask import Blueprint, render_template
from flask_login import login_required
from sqlalchemy import func

from ..models import db, PDSatResponse

reports_satisfaction_bp = Blueprint(
    "reports_satisfaction",
    __name__,
    url_prefix="/reports/satisfaction"
)


@reports_satisfaction_bp.route("/stats")
@login_required
def satisfaction_stats():
    """
    滿意度統計報表
    """
    # 統計 overall_score 分布 (1~5 分)
    score_counts = (
        db.session.query(PDSatResponse.overall_score, func.count(PDSatResponse.id))
        .group_by(PDSatResponse.overall_score)
        .all()
    )

    # 總樣本數
    total = sum(c for _, c in score_counts)

    # 整理成 {分數:數量, ...}
    data = {i: 0 for i in range(1, 6)}
    for score, count in score_counts:
        if score:
            data[score] = count

    # 平均分數
    avg_score = (
        db.session.query(func.avg(PDSatResponse.overall_score))
        .scalar() or 0
    )
    avg_score = round(avg_score, 2)

    return render_template(
        "reports/satisfaction_stats.html",
        active_page="reports",
        ACTIVE_MENU="reports",
        ACTIVE_SUBMENU="satisfaction",
        ACTIVE_ITEM="sat_stats",
        data=data,
        total=total,
        avg_score=avg_score
    )
