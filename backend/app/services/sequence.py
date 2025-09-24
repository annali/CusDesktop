# backend/services/sequence.py
from datetime import datetime
from sqlalchemy import select, update
from ..models import db
from ..models import PDSequence

def _today_tokens():
    now = datetime.utcnow()
    return {
        'YYYY': now.strftime('%Y'),
        'MM': now.strftime('%m'),
        'DD': now.strftime('%d'),
    }

def next_form_no(code: str) -> str:
    with db.session.begin_nested():
        seq: PDSequence = db.session.execute(
            select(PDSequence).where(PDSequence.code==code).with_for_update()
        ).scalar_one()
        # reset rule (簡版: 以日期字串變化視為 reset 觸發點)
        tokens = _today_tokens()
        prefix = seq.prefix
        for k,v in tokens.items():
            prefix = prefix.replace('{%s}'%k, v)
        # 若 prefix 改變代表一天新字首，可選擇歸零；這裡簡化為日切歸零
        if seq.reset_rule == 'DAILY' and not str(seq.current_no).startswith(tokens['YYYY']):  # 可自定更嚴謹的日切判斷
            seq.current_no = 0
        seq.current_no += 1
        db.session.flush()
        return f"{prefix}{seq.current_no:04d}"
