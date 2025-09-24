# models.py
from __future__ import annotations
from datetime import datetime
import secrets

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from sqlalchemy import func, select, event

db = SQLAlchemy()

# =========================================================
# =============== 共用 / 既有模組（原樣保留） ================
# =========================================================

class PDType(db.Model):
    __tablename__ = 'pd_type_master'
    type_sid = db.Column(db.Integer, primary_key=True)
    type_group = db.Column(db.String(50), nullable=False, index=True)
    type_name  = db.Column(db.String(100), nullable=False)
    memo       = db.Column(db.String(255))
    status     = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)


# ====== 表單範本主檔 ======
class PDFormTemplates(db.Model):
    __tablename__ = 'pd_form_template'
    __table_args__ = (
        db.UniqueConstraint('form_template_code', name='uq_pd_form_template_code'),
    )

    form_template_sid = db.Column(db.Integer, primary_key=True)
    form_template_group = db.Column(db.String(50), nullable=False, index=True)  # e.g., 'attendance','incident'
    form_template_name  = db.Column(db.String(100), nullable=False)
    form_template_code  = db.Column(db.String(100), nullable=False)             # e.g., 'ATD','INC'
    form_template_json  = db.Column(db.Text, nullable=False)
    memo = db.Column(db.String(255))
    form_template_seq = db.Column(db.Integer, default=0, server_default=db.text('0'))
    status = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    creator = db.relationship('Users', foreign_keys=[create_usr], viewonly=True, lazy='joined')
    updater = db.relationship('Users', foreign_keys=[update_usr], viewonly=True, lazy='joined')


class PDTicket(db.Model):
    __tablename__ = 'pd_ticket_master'
    ticket_sid   = db.Column(db.Integer, primary_key=True)
    project_sid  = db.Column(db.Integer, db.ForeignKey('pd_project_master.project_sid'), nullable=False, index=True)
    ticket_name  = db.Column(db.String(255), nullable=False)
    ticket_no    = db.Column(db.String(50), nullable=False, index=True)
    source_type  = db.Column(db.Integer, nullable=False)
    ticket_type  = db.Column(db.Integer, db.ForeignKey('pd_type_master.type_sid'))
    ticket_description = db.Column(db.String(255))
    ticket_status = db.Column(db.String(50), nullable=False, index=True)
    ticket_priority = db.Column(db.String(50), nullable=False)
    memo = db.Column(db.String(255))
    is_converted = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('0'), index=True)

    # 客戶資訊
    customer_dep_sid = db.Column(db.Integer, db.ForeignKey('sys_dep_master.dep_sid'))
    customer_contact_name = db.Column(db.String(120))

    status = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship(
        'PDProject',
        primaryjoin='PDTicket.project_sid == PDProject.project_sid',
        lazy='joined',
        backref=db.backref('tickets', lazy='dynamic')
    )
    ticket_type_def = db.relationship('PDType',
        primaryjoin='PDTicket.ticket_type == PDType.type_sid',
        lazy='joined'
    )
    creator_user = db.relationship('Users',
        primaryjoin='PDTicket.create_usr == Users.user_sid',
        foreign_keys=[create_usr],
        lazy='joined'
    )
    customer_dep = db.relationship('Deps', foreign_keys=[customer_dep_sid], lazy='joined', viewonly=True)


class PDProjectMember(db.Model):
    __tablename__ = 'pd_project_member'
    __table_args__ = (
        db.UniqueConstraint('project_sid', 'user_sid', name='uq_project_member'),
    )
    project_member_sid = db.Column(db.Integer, primary_key=True)
    project_sid = db.Column(db.Integer, db.ForeignKey('pd_project_master.project_sid'), nullable=False, index=True)
    user_sid    = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False, index=True)
    role        = db.Column(db.String(50))
    status      = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr  = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt   = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr  = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt   = db.Column(db.DateTime, default=datetime.utcnow)

    project = db.relationship(
        'PDProject',
        foreign_keys=[project_sid],
        backref=db.backref('members', lazy='dynamic')
    )
    user = db.relationship('Users', foreign_keys=[user_sid], lazy='joined')
    creator = db.relationship('Users', foreign_keys=[create_usr], lazy='joined', viewonly=True)
    updater = db.relationship('Users', foreign_keys=[update_usr], lazy='joined', viewonly=True)


class PDProject(db.Model):
    __tablename__ = 'pd_project_master'
    project_sid = db.Column(db.Integer, primary_key=True)
    project_no  = db.Column(db.String(50), nullable=False, index=True)
    project_name = db.Column(db.String(255), nullable=False)
    project_manager_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    project_description = db.Column(db.String(255), nullable=False)
    project_customer_org = db.Column(db.Integer, db.ForeignKey('sys_dep_master.dep_sid'))

    project_type = db.Column(db.Integer, db.ForeignKey('pd_type_master.type_sid'))
    project_type_def = db.relationship('PDType',
        primaryjoin='PDProject.project_type == PDType.type_sid',
        lazy='joined'
    )

    start_dt = db.Column(db.DateTime, default=datetime.utcnow)
    end_dt   = db.Column(db.DateTime, default=datetime.utcnow)
    actual_end_dt = db.Column(db.DateTime, default=datetime.utcnow)
    project_budget = db.Column(db.Integer)
    project_status = db.Column(db.String(50), nullable=False, index=True)
    project_priority = db.Column(db.String(50), nullable=False)
    project_customer_name = db.Column(db.String(255))
    project_dep_name = db.Column(db.String(255))
    memo = db.Column(db.String(255))
    status = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    manager = db.relationship('Users', foreign_keys=[project_manager_usr], lazy='joined', viewonly=True)


class PDCase(db.Model):
    __tablename__ = 'pd_case_master'
    case_sid = db.Column(db.Integer, primary_key=True)
    project_sid = db.Column(db.Integer, db.ForeignKey('pd_project_master.project_sid'), nullable=False, index=True)
    ticket_sid  = db.Column(db.Integer, db.ForeignKey('pd_ticket_master.ticket_sid'))
    related_cases = db.Column(db.Text)
    case_name = db.Column(db.String(255), nullable=False)
    case_no   = db.Column(db.String(50), nullable=False, index=True)
    case_type = db.Column(db.Integer, nullable=False)
    case_description = db.Column(db.Text, nullable=False)
    case_assignee_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    due_dt = db.Column(db.DateTime, default=datetime.utcnow)
    case_closed_dt = db.Column(db.DateTime)
    case_status = db.Column(db.Integer, index=True)
    case_priority = db.Column(db.Integer)
    memo = db.Column(db.Text)
    status = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    ticket = db.relationship('PDTicket', backref=db.backref('cases', lazy='dynamic'))
    project = db.relationship('PDProject', backref=db.backref('cases', lazy='dynamic'))
    assignee = db.relationship('Users', foreign_keys=[case_assignee_usr], lazy='joined', viewonly=True)


class PDDispatch(db.Model):
    __tablename__ = 'pd_dispatch_master'
    dispatch_sid = db.Column(db.Integer, primary_key=True)
    case_sid = db.Column(db.Integer, db.ForeignKey('pd_case_master.case_sid'), nullable=False, index=True)
    dispatch_type = db.Column(db.Integer, nullable=False)
    dispatch_name = db.Column(db.String(255), nullable=False)
    dispatch_no   = db.Column(db.String(50), nullable=False, index=True)
    dispatch_description = db.Column(db.String(255))
    dispatch_status = db.Column(db.String(50), nullable=False, index=True)
    scheduled_dt  = db.Column(db.DateTime, default=datetime.utcnow)
    dispatch_assignee_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))

    start_dt = db.Column(db.DateTime, default=datetime.utcnow)
    finished_dt = db.Column(db.DateTime, default=datetime.utcnow)
    dispatch_priority = db.Column(db.String(50), nullable=False)
    memo = db.Column(db.String(255))
    status = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    case = db.relationship('PDCase', backref=db.backref('dispatches', lazy='dynamic'))
    assignee = db.relationship('Users', foreign_keys=[dispatch_assignee_usr], viewonly=True, lazy='joined')


class PDComment(db.Model):
    __tablename__ = 'pd_comment_master'
    comment_sid = db.Column(db.Integer, primary_key=True)
    ref_type = db.Column(db.String(50), nullable=False, index=True)  # 'ticket'/'case'/'dispatch'/'X_form'
    ref_sid  = db.Column(db.Integer, nullable=False, index=True)
    author_sid = db.Column(db.Integer, nullable=False)
    content    = db.Column(db.Text, nullable=False)
    status = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)
    # 提醒：author_sid 未加 FK（沿用你現有設計）


class PDAttachment(db.Model):
    __tablename__ = 'pd_attachment_master'
    attachmen_sid = db.Column(db.Integer, primary_key=True)
    ref_type = db.Column(db.String(50), nullable=False, index=True)
    ref_sid  = db.Column(db.Integer, nullable=False, index=True)
    attachment_code = db.Column(db.String(255))
    attachment_name = db.Column(db.String(100), nullable=False)
    attachment_size = db.Column(db.String(100), nullable=False)
    memo = db.Column(db.String(255))
    status = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)


class PDActivityLog(db.Model):
    __tablename__ = 'pd_activity_log'
    activity_log_sid = db.Column(db.Integer, primary_key=True, autoincrement=True)
    ref_type = db.Column(db.String(50), nullable=False, index=True)
    ref_sid  = db.Column(db.Integer, nullable=False, index=True)
    action   = db.Column(db.String(10), nullable=False)  # create/update/submit/approve/sign/export/return
    field_name = db.Column(db.String(50), nullable=False)
    old_value = db.Column(db.Text)
    new_value = db.Column(db.Text)
    message   = db.Column(db.Text, nullable=False)
    status = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


# =========================================================
# ==================== 系統使用者 / 部門 ===================
# =========================================================

class Users(UserMixin, db.Model):
    __tablename__ = 'sys_user'
    user_sid = db.Column(db.Integer, primary_key=True)
    login_account = db.Column(db.String(100), nullable=False, index=True, unique=True)
    password = db.Column(db.String(255), nullable=False)
    username = db.Column(db.String(100), nullable=False)
    email    = db.Column(db.String(100), nullable=False, index=True)
    dep_sid  = db.Column(db.Integer, nullable=False)  # 與 Deps 關聯（保留你現有設計）
    memo = db.Column(db.String(255))
    verify = db.Column(db.Integer, nullable=False, default=0, server_default=db.text('0'))
    last_login = db.Column(db.DateTime, default=datetime.utcnow)
    ver_code   = db.Column(db.String(32))
    db_private_key = db.Column(db.String(255))
    status = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=True)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    # def set_password(self, raw: str):
    #     self.password = generate_password_hash(raw)
    #
    # def check_password(self, raw: str) -> bool:
    #     return check_password_hash(self.password, raw)
    # --- 關鍵修正：讓 Flask-Login 取得使用者 ID ---
    # def get_id(self) -> str:
    #     # Flask-Login 需要回傳可序列化為字串的主鍵
    #     return str(self.user_sid)

    def get_id(self):
        return str(self.user_sid)
    # （可選）相容舊套件邏輯：提供 id 屬性，UserMixin 也會讀這個
    @property
    def id(self) -> int:
        return self.user_sid

    def set_password(self, raw: str):
        self.password = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password, raw)


class Deps(db.Model):
    __tablename__ = 'sys_dep_master'
    dep_sid = db.Column(db.Integer, primary_key=True)
    dep_name = db.Column(db.String(100), nullable=False)
    top_dep  = db.Column(db.Integer)   # 上層部門 SID（自關聯，可後續以 FK 強化）
    dep_manager = db.Column(db.Integer)  # manager user_sid
    dep_level   = db.Column(db.Integer, nullable=False)
    memo = db.Column(db.String(255))
    status = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)


# =========================================================
# ================== 滿意度（既有） =======================
# =========================================================

class PDSatFormVersion(db.Model):
    __tablename__ = 'pd_sat_form_version'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(120), nullable=False)
    lang = db.Column(db.String(10), default='zh-TW', index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.text('1'))
    schema_json = db.Column(db.Text, nullable=False)
    memo = db.Column(db.String(255))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    creator = db.relationship('Users', foreign_keys=[create_usr], viewonly=True, lazy='joined')
    updater = db.relationship('Users', foreign_keys=[update_usr], viewonly=True, lazy='joined')


class PDSatInvite(db.Model):
    __tablename__ = 'pd_sat_invite'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    form_version_id = db.Column(db.Integer, db.ForeignKey('pd_sat_form_version.id'), nullable=False)
    case_sid = db.Column(db.Integer, db.ForeignKey('pd_case_master.case_sid'))
    project_sid = db.Column(db.Integer, db.ForeignKey('pd_project_master.project_sid'))

    customer_name = db.Column(db.String(120))
    customer_email = db.Column(db.String(200))
    channel = db.Column(db.String(20), default='email')        # email / line / sms
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    status = db.Column(db.String(20), default='sent', index=True)  # sent/opened/answered/expired/revoked
    send_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expire_at = db.Column(db.DateTime)
    retries = db.Column(db.Integer, default=0)

    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    form_version = db.relationship('PDSatFormVersion', lazy='joined')
    case = db.relationship('PDCase', foreign_keys=[case_sid], lazy='joined')
    project = db.relationship('PDProject', foreign_keys=[project_sid], lazy='joined')

    @staticmethod
    def gen_token() -> str:
        return secrets.token_urlsafe(32)


class PDSatResponse(db.Model):
    __tablename__ = 'pd_sat_response'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    invite_id = db.Column(db.Integer, db.ForeignKey('pd_sat_invite.id'), nullable=False)
    form_version_id = db.Column(db.Integer, db.ForeignKey('pd_sat_form_version.id'), nullable=False)

    overall_score = db.Column(db.Integer)   # 1~5
    nps = db.Column(db.Integer)             # 0~10
    csi = db.Column(db.Float)               # 客製加權分
    comment_text = db.Column(db.Text)
    is_anonymous = db.Column(db.Boolean, default=True, server_default=db.text('1'))

    source_ip = db.Column(db.String(64))
    ua = db.Column(db.String(255))
    answered_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    invite = db.relationship('PDSatInvite', lazy='joined')
    form_version = db.relationship('PDSatFormVersion', lazy='joined')


class PDSatAnswer(db.Model):
    __tablename__ = 'pd_sat_answer'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    response_id = db.Column(db.Integer, db.ForeignKey('pd_sat_response.id'), nullable=False, index=True)
    question_key = db.Column(db.String(120), nullable=False)
    answer_value = db.Column(db.Text)
    weight = db.Column(db.Float, default=1.0)

    response = db.relationship('PDSatResponse', backref=db.backref('answers', lazy='dynamic'))


# =========================================================
# ============== 表單管理（Form module） ==================
# =========================================================

class PDFormTemplateVersion(db.Model):
    __tablename__ = 'pd_form_template_version'
    __table_args__ = (
        db.UniqueConstraint('template_sid', 'version_no', name='uq_form_template_version'),
    )

    version_sid = db.Column(db.Integer, primary_key=True)
    template_sid = db.Column(db.Integer, db.ForeignKey('pd_form_template.form_template_sid'), nullable=False, index=True)
    version_no   = db.Column(db.Integer, nullable=False)  # 1,2,3...
    is_published = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('0'), index=True)
    publish_dt   = db.Column(db.DateTime)

    schema_json    = db.Column(db.Text, nullable=False)
    ui_schema_json = db.Column(db.Text)
    memo = db.Column(db.String(255))

    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    template = db.relationship('PDFormTemplates',
                               backref=db.backref('versions', lazy='dynamic', cascade='all, delete-orphan'))
    creator = db.relationship('Users', foreign_keys=[create_usr], viewonly=True, lazy='joined')
    updater = db.relationship('Users', foreign_keys=[update_usr], viewonly=True, lazy='joined')


class PDFormSignerRule(db.Model):
    __tablename__ = 'pd_form_signer_rule'
    __table_args__ = (
        db.Index('ix_signer_rule_version_order', 'version_sid', 'order_no'),
    )

    rule_sid   = db.Column(db.Integer, primary_key=True)
    version_sid = db.Column(db.Integer, db.ForeignKey('pd_form_template_version.version_sid'), nullable=False, index=True)

    order_no   = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    parallel_group = db.Column(db.Integer, nullable=False, default=0, server_default=db.text('0'))  # 0=非並簽
    role       = db.Column(db.String(30), nullable=False)  # 'customer'/'engineer'/'pm'
    title      = db.Column(db.String(120))
    required   = db.Column(db.Boolean, nullable=False, default=True, server_default=db.text('1'))
    mode       = db.Column(db.String(20), nullable=False, default='draw', server_default=db.text("'draw'"))
    # draw / upload / type / cert

    status     = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    version = db.relationship('PDFormTemplateVersion',
                              backref=db.backref('signer_rules', lazy='dynamic', cascade='all, delete-orphan'))
    creator = db.relationship('Users', foreign_keys=[create_usr], viewonly=True, lazy='joined')
    updater = db.relationship('Users', foreign_keys=[update_usr], viewonly=True, lazy='joined')


class PDSequence(db.Model):
    __tablename__ = 'pd_sequence'
    seq_sid    = db.Column(db.Integer, primary_key=True, autoincrement=True)
    code       = db.Column(db.String(30), nullable=False, unique=True)
    prefix     = db.Column(db.String(60), nullable=False)
    current_no = db.Column(db.Integer, nullable=False, default=0, server_default=db.text('0'))
    reset_rule = db.Column(db.String(10), nullable=False, default='DAILY', server_default=db.text("'DAILY'"))
    status     = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime)

    @staticmethod
    def next_sequence_no(session, code: str, prefix: str) -> str:
        seq: PDSequence | None = session.execute(
            db.select(PDSequence).where(PDSequence.code == code).with_for_update()
        ).scalar_one_or_none()

        if not seq:
            seq = PDSequence(code=code, prefix=prefix, current_no=0, create_usr=0)
            session.add(seq)
            session.flush()

        seq.current_no += 1
        session.flush()

        today_str = datetime.utcnow().strftime("%Y%m%d")
        running = f"{seq.current_no:05d}"
        return f"{seq.prefix}{today_str}-{running}"


class PDFormInstance(db.Model):
    __tablename__ = 'pd_form_instance'
    __table_args__ = (
        db.UniqueConstraint('form_no', name='uq_pd_form_instance_no'),
        db.Index('ix_form_instance_refs', 'project_sid', 'ticket_sid', 'case_sid', 'dispatch_sid'),
        db.Index('ix_form_instance_state_dates', 'state', 'create_dt', 'submit_dt', 'approved_dt'),
    )

    form_sid = db.Column(db.Integer, primary_key=True)
    form_no  = db.Column(db.String(60), nullable=False)
    template_sid = db.Column(db.Integer, db.ForeignKey('pd_form_template.form_template_sid'), nullable=False, index=True)
    version_sid  = db.Column(db.Integer, db.ForeignKey('pd_form_template_version.version_sid'), nullable=False, index=True)
    version_no  = db.Column(db.Integer, nullable=False, server_default=db.text('0'), index=True)

    project_sid  = db.Column(db.Integer, db.ForeignKey('pd_project_master.project_sid'), index=True)
    ticket_sid   = db.Column(db.Integer, db.ForeignKey('pd_ticket_master.ticket_sid'), index=True)
    case_sid     = db.Column(db.Integer, db.ForeignKey('pd_case_master.case_sid'), index=True)
    dispatch_sid = db.Column(db.Integer, db.ForeignKey('pd_dispatch_master.dispatch_sid'), index=True)

    state   = db.Column(db.String(20), nullable=False, default='draft', server_default=db.text("'draft'"), index=True)
    signed  = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('0'), index=True)
    attachments_count = db.Column(db.Integer, nullable=False, default=0, server_default=db.text('0'))

    form_data_json = db.Column(db.Text, nullable=False)
    memo = db.Column(db.String(255))

    pdf_path = db.Column(db.String(255))
    verify_token = db.Column(db.String(64), index=True)
    payload_hash = db.Column(db.String(64), index=True)

    status = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False, index=True)
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    submit_dt  = db.Column(db.DateTime)
    approved_by = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    approved_dt = db.Column(db.DateTime)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    template = db.relationship('PDFormTemplates', lazy='joined')
    version  = db.relationship('PDFormTemplateVersion', lazy='joined')

    project  = db.relationship('PDProject', foreign_keys=[project_sid],
                               backref=db.backref('X_form', lazy='dynamic'))
    ticket   = db.relationship('PDTicket', foreign_keys=[ticket_sid],
                               backref=db.backref('X_form', lazy='dynamic'))
    case     = db.relationship('PDCase', foreign_keys=[case_sid],
                               backref=db.backref('X_form', lazy='dynamic'))
    dispatch = db.relationship('PDDispatch', foreign_keys=[dispatch_sid],
                               backref=db.backref('X_form', lazy='dynamic'))

    creator  = db.relationship('Users', foreign_keys=[create_usr], viewonly=True, lazy='joined')
    approver = db.relationship('Users', foreign_keys=[approved_by], viewonly=True, lazy='joined')
    updater  = db.relationship('Users', foreign_keys=[update_usr], viewonly=True, lazy='joined')


class PDFormSignature(db.Model):
    __tablename__ = 'pd_form_signature'
    __table_args__ = (
        db.Index('ix_form_signature_form_role', 'form_sid', 'signer_role'),
        db.Index('ix_form_signature_rule', 'form_sid', 'rule_sid'),
    )

    signature_sid = db.Column(db.Integer, primary_key=True)
    form_sid = db.Column(db.Integer, db.ForeignKey('pd_form_instance.form_sid'), nullable=False, index=True)
    rule_sid = db.Column(db.Integer, db.ForeignKey('pd_form_signer_rule.rule_sid'))

    signer_role = db.Column(db.String(30), nullable=False)   # 'customer'/'engineer'/'pm'
    signer_name = db.Column(db.String(120))
    signer_title = db.Column(db.String(120))
    sign_time = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    sign_image_path = db.Column(db.String(255))
    sign_vector_json = db.Column(db.Text)
    sign_ip = db.Column(db.String(64))
    sign_device_info = db.Column(db.String(255))
    sign_lat = db.Column(db.Float)
    sign_lng = db.Column(db.Float)

    is_valid = db.Column(db.Boolean, nullable=False, default=True, server_default=db.text('1'))
    payload_hash = db.Column(db.String(64), index=True)
    evidence_json = db.Column(db.Text)

    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    form = db.relationship('PDFormInstance', backref=db.backref('signatures', lazy='dynamic'))
    rule = db.relationship('PDFormSignerRule', lazy='joined')
    creator = db.relationship('Users', foreign_keys=[create_usr], viewonly=True, lazy='joined')


class PDFormAttachment(db.Model):
    __tablename__ = 'pd_form_attachment'
    __table_args__ = (
        db.Index('ix_form_attachment_formsid', 'form_sid'),
    )

    form_attachment_sid = db.Column(db.Integer, primary_key=True)
    form_sid = db.Column(db.Integer, db.ForeignKey('pd_form_instance.form_sid'), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(30))  # photo/pdf/etc.
    label = db.Column(db.String(120))
    uploaded_by = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    upload_dt = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    status = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    memo = db.Column(db.String(255))

    form = db.relationship('PDFormInstance', backref=db.backref('form_attachments', lazy='dynamic'))
    uploader = db.relationship('Users', foreign_keys=[uploaded_by], viewonly=True, lazy='joined')


# =========================================================
# ====== 計算欄位：範本「最新已發佈版號」 column_property ======
# =========================================================
PDFormTemplates.latest_published_version_no = db.column_property(
    select(func.max(PDFormTemplateVersion.version_no))
    .where(PDFormTemplateVersion.template_sid == PDFormTemplates.form_template_sid)
    .where(PDFormTemplateVersion.is_published.is_(True))
    .correlate_except(PDFormTemplateVersion)
    .scalar_subquery()
)

# =========================================================
# ================== 便利函式與事件 =======================
# =========================================================

def next_form_no(session, template: PDFormTemplates) -> str:
    code = template.form_template_code
    prefix = f"{code}-"
    return PDSequence.next_sequence_no(session, code=code, prefix=prefix)

@event.listens_for(PDFormInstance, "before_insert")
def _auto_fill_form_no_and_version(mapper, connect, target: PDFormInstance):
    if not target.form_no:
        from sqlalchemy.orm import Session
        sess = Session(bind=connect)
        try:
            template = sess.get(PDFormTemplates, target.template_sid)
            if template:
                target.form_no = next_form_no(sess, template)
            else:
                target.form_no = f"FORM-{datetime.utcnow().strftime('%Y%m%d-%H%M%S%f')}"
        finally:
            sess.close()

    if not target.version_no and target.version_sid:
        target.version_no = connect.execute(
            db.select(PDFormTemplateVersion.version_no).where(
                PDFormTemplateVersion.version_sid == target.version_sid
            )
        ).scalar_one_or_none() or 0


# =========================================================
# ============== 系統管理（公司 / 角色 / 權限） =============
# =========================================================

class Company(db.Model):
    __tablename__ = 'sys_company_master'
    comp_sid = db.Column(db.Integer, primary_key=True)
    comp_code = db.Column(db.String(30), nullable=False, unique=True, index=True)
    comp_name = db.Column(db.String(120), nullable=False)
    tax_id    = db.Column(db.String(20))
    phone     = db.Column(db.String(30))
    email     = db.Column(db.String(120))
    address   = db.Column(db.String(255))
    memo      = db.Column(db.String(255))

    status     = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    creator = db.relationship('Users', foreign_keys=[create_usr], viewonly=True, lazy='joined')
    updater = db.relationship('Users', foreign_keys=[update_usr], viewonly=True, lazy='joined')


class Role(db.Model):
    __tablename__ = 'sys_role'
    role_sid = db.Column(db.Integer, primary_key=True)
    role_code = db.Column(db.String(50), nullable=False, unique=True, index=True)
    role_name = db.Column(db.String(120), nullable=False)
    memo      = db.Column(db.String(255))

    status     = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)


class UserRole(db.Model):
    __tablename__ = 'sys_user_role'
    __table_args__ = (
        db.UniqueConstraint('user_sid', 'role_sid', name='uq_user_role'),
    )
    id       = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_sid = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), nullable=False, index=True)
    role_sid = db.Column(db.Integer, db.ForeignKey('sys_role.role_sid'), nullable=False, index=True)

    status     = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # 重要：指定 foreign_keys，避免與 create_usr 混淆
    user    = db.relationship('Users', foreign_keys=[user_sid], lazy='joined')
    role    = db.relationship('Role',  foreign_keys=[role_sid], lazy='joined')
    creator = db.relationship('Users', foreign_keys=[create_usr], lazy='joined', viewonly=True)


class Menu(db.Model):
    __tablename__ = 'sys_menu'
    menu_sid   = db.Column(db.Integer, primary_key=True)
    parent_sid = db.Column(db.Integer, db.ForeignKey('sys_menu.menu_sid'), index=True)
    menu_code  = db.Column(db.String(60), nullable=False, unique=True, index=True)
    menu_name  = db.Column(db.String(120), nullable=False)
    route_path = db.Column(db.String(255), index=True)    # e.g. /admin/users
    endpoint   = db.Column(db.String(120), index=True)    # Flask endpoint name
    icon       = db.Column(db.String(60))
    seq        = db.Column(db.Integer, default=0, server_default=db.text('0'))
    is_visible = db.Column(db.Boolean, nullable=False, default=True, server_default=db.text('1'))
    is_admin   = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('0'))
    menu_group = db.Column(db.String(100))
    menu_order = db.Column(db.Integer)
    status     = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    # 🔑 新增這個
    platform = db.Column(db.String(50), nullable=False, default="default")
    parent = db.relationship('Menu', remote_side=[menu_sid], backref=db.backref('children', lazy='dynamic'))



class RoleMenu(db.Model):
    __tablename__ = 'sys_role_menu'
    __table_args__ = (
        db.UniqueConstraint('role_sid', 'menu_sid', name='uq_role_menu'),
    )
    id       = db.Column(db.Integer, primary_key=True, autoincrement=True)
    role_sid = db.Column(db.Integer, db.ForeignKey('sys_role.role_sid'), nullable=False, index=True)
    menu_sid = db.Column(db.Integer, db.ForeignKey('sys_menu.menu_sid'), nullable=False, index=True)

    can_create = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('0'))
    can_read   = db.Column(db.Boolean, nullable=False, default=True,  server_default=db.text('1'))
    can_update = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('0'))
    can_delete = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('0'))

    status     = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    role    = db.relationship('Role',  foreign_keys=[role_sid], lazy='joined')
    menu    = db.relationship('Menu',  foreign_keys=[menu_sid], lazy='joined')
    creator = db.relationship('Users', foreign_keys=[create_usr], lazy='joined', viewonly=True)


class Permission(db.Model):
    __tablename__ = 'sys_permission'
    perm_sid   = db.Column(db.Integer, primary_key=True)
    perm_code  = db.Column(db.String(100), nullable=False, unique=True, index=True)
    resource   = db.Column(db.String(100), nullable=False, index=True)
    action     = db.Column(db.String(50),  nullable=False, index=True)  # create/read/update/delete/export/approve...
    endpoint   = db.Column(db.String(120))
    http_method= db.Column(db.String(10))
    memo       = db.Column(db.String(255))

    status     = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)


class RolePermission(db.Model):
    __tablename__ = 'sys_role_permission'
    __table_args__ = (
        db.UniqueConstraint('role_sid', 'perm_sid', name='uq_role_perm'),
    )
    id       = db.Column(db.Integer, primary_key=True, autoincrement=True)
    role_sid = db.Column(db.Integer, db.ForeignKey('sys_role.role_sid'), nullable=False, index=True)
    perm_sid = db.Column(db.Integer, db.ForeignKey('sys_permission.perm_sid'), nullable=False, index=True)

    allow     = db.Column(db.Boolean, nullable=False, default=True, server_default=db.text('1'))
    create_usr= db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    create_dt = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    role    = db.relationship('Role',        foreign_keys=[role_sid], lazy='joined')
    perm    = db.relationship('Permission',  foreign_keys=[perm_sid], lazy='joined')
    creator = db.relationship('Users',       foreign_keys=[create_usr], lazy='joined', viewonly=True)


# =========================================================
# ============== 安全 / 稽核 / 系統參數等 ==================
# =========================================================

class LoginAudit(db.Model):
    __tablename__ = 'sys_login_audit'
    id        = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_sid  = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'), index=True)
    login_at  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ip        = db.Column(db.String(64))
    user_agent= db.Column(db.String(255))
    success   = db.Column(db.Boolean, nullable=False, default=True, server_default=db.text('1'))
    reason    = db.Column(db.String(120))  # wrong_password / locked / 2fa_failed / etc.

    user = db.relationship('Users', foreign_keys=[user_sid], lazy='joined', viewonly=True)


class SystemParam(db.Model):
    __tablename__ = 'sys_param'
    key   = db.Column(db.String(120), primary_key=True)   # e.g. 'SLA_DEFAULT_RESPONSE_HOURS'
    value = db.Column(db.String(1024), nullable=False)
    memo  = db.Column(db.String(255))
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    updater = db.relationship('Users', foreign_keys=[update_usr], viewonly=True, lazy='joined')


class Announcement(db.Model):
    __tablename__ = 'sys_announcement'
    ann_sid  = db.Column(db.Integer, primary_key=True)
    title    = db.Column(db.String(200), nullable=False)
    content  = db.Column(db.Text, nullable=False)
    start_dt = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    end_dt   = db.Column(db.DateTime)
    severity = db.Column(db.String(20), default='info')   # info/warn/critical
    audience = db.Column(db.String(30), default='all')    # all/admin/user/guest

    status     = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    creator = db.relationship('Users', foreign_keys=[create_usr], viewonly=True, lazy='joined')
    updater = db.relationship('Users', foreign_keys=[update_usr], viewonly=True, lazy='joined')


class ApiKey(db.Model):
    __tablename__ = 'sys_api_key'
    key_sid   = db.Column(db.Integer, primary_key=True)
    name      = db.Column(db.String(120), nullable=False)
    token     = db.Column(db.String(128), nullable=False, unique=True, index=True)
    scopes    = db.Column(db.String(255))
    owner_sid = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    expire_dt = db.Column(db.DateTime)

    status     = db.Column(db.Integer, nullable=False, default=1, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    owner   = db.relationship('Users', foreign_keys=[owner_sid],  viewonly=True, lazy='joined')
    creator = db.relationship('Users', foreign_keys=[create_usr], viewonly=True, lazy='joined')
    updater = db.relationship('Users', foreign_keys=[update_usr], viewonly=True, lazy='joined')


class SchedulerJob(db.Model):
    __tablename__ = 'sys_scheduler_job'
    job_sid   = db.Column(db.Integer, primary_key=True)
    job_code  = db.Column(db.String(120), nullable=False, unique=True, index=True)
    job_name  = db.Column(db.String(200), nullable=False)
    cron_expr = db.Column(db.String(60), nullable=False)     # 例：'0 9 * * *'
    job_type  = db.Column(db.String(50), default='python')   # python/http/sql…
    payload   = db.Column(db.Text)                           # 任務設定 JSON
    last_run_at = db.Column(db.DateTime)
    last_status = db.Column(db.String(20))                   # success/failed
    last_message= db.Column(db.Text)

    is_active  = db.Column(db.Boolean, nullable=False, default=True, server_default=db.text('1'))
    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    update_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    update_dt  = db.Column(db.DateTime, default=datetime.utcnow)

    creator = db.relationship('Users', foreign_keys=[create_usr], viewonly=True, lazy='joined')
    updater = db.relationship('Users', foreign_keys=[update_usr], viewonly=True, lazy='joined')


class ImportExportJob(db.Model):
    __tablename__ = 'sys_io_job'
    io_sid   = db.Column(db.Integer, primary_key=True)
    io_type  = db.Column(db.String(20), nullable=False)      # import/export
    target   = db.Column(db.String(60), nullable=False)      # users/deps/company/ticket/...
    file_path= db.Column(db.String(255))
    params   = db.Column(db.Text)                            # JSON 參數
    status   = db.Column(db.String(20), default='queued', index=True)  # queued/running/success/failed
    message  = db.Column(db.Text)

    create_usr = db.Column(db.Integer, db.ForeignKey('sys_user.user_sid'))
    create_dt  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    finish_dt  = db.Column(db.DateTime)

    creator = db.relationship('Users', foreign_keys=[create_usr], viewonly=True, lazy='joined')


class Roles(db.Model):
    __tablename__ = 'sys_roles'
    role_id = db.Column(db.Integer, primary_key=True)
    role_name = db.Column(db.String(50), unique=True, nullable=False)
    memo = db.Column(db.String(255))
    create_dt = db.Column(db.DateTime, default=datetime.utcnow)
    update_dt = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)