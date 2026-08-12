from datetime import datetime, timezone

from flask_login import UserMixin

from .extensions import db


def utc_now():
    return datetime.now(timezone.utc)


class AccessProfile(db.Model):
    __tablename__ = "access_profiles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    can_manage_users = db.Column(db.Boolean, default=False, nullable=False)
    can_manage_settings = db.Column(db.Boolean, default=False, nullable=False)
    can_reset_data = db.Column(db.Boolean, default=False, nullable=False)
    can_work_tickets = db.Column(db.Boolean, default=False, nullable=False)
    can_view_reports = db.Column(db.Boolean, default=False, nullable=False)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, index=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    profile_id = db.Column(db.Integer, db.ForeignKey("access_profiles.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    profile = db.relationship("AccessProfile", backref="users")

    @property
    def is_active(self):
        return self.active

    def has_permission(self, permission_name):
        return bool(self.profile and getattr(self.profile, permission_name, False))


class Branch(db.Model):
    __tablename__ = "branches"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    kind = db.Column(db.String(60), nullable=False, default="Geral")
    active = db.Column(db.Boolean, default=True, nullable=False)


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

    fields = db.relationship(
        "DynamicField",
        backref="category",
        cascade="all, delete-orphan",
        order_by="DynamicField.sort_order",
    )


class DynamicField(db.Model):
    __tablename__ = "dynamic_fields"

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    field_type = db.Column(db.String(40), nullable=False, default="text")
    required = db.Column(db.Boolean, default=False, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)


class Ticket(db.Model):
    __tablename__ = "tickets"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    priority = db.Column(db.String(40), nullable=False, default="Media")
    status = db.Column(db.String(40), nullable=False, default="Aberta", index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False, index=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"), nullable=True, index=True)
    requester_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    due_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    pause_started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    total_paused_seconds = db.Column(db.Integer, default=0, nullable=False)
    resolution_note = db.Column(db.Text, nullable=True)
    initial_file = db.Column(db.String(300), nullable=True)
    final_file = db.Column(db.String(300), nullable=True)
    custom_data = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    category = db.relationship("Category")
    branch = db.relationship("Branch")
    requester = db.relationship("User", foreign_keys=[requester_id])
    assignee = db.relationship("User", foreign_keys=[assignee_id])
    history = db.relationship(
        "TicketHistory",
        backref="ticket",
        cascade="all, delete-orphan",
        order_by="desc(TicketHistory.created_at)",
    )
    comments = db.relationship(
        "TicketComment",
        backref="ticket",
        cascade="all, delete-orphan",
        order_by="TicketComment.created_at",
    )


class TicketHistory(db.Model):
    __tablename__ = "ticket_history"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    action = db.Column(db.String(100), nullable=False)
    note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    user = db.relationship("User")


class TicketComment(db.Model):
    __tablename__ = "ticket_comments"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False)

    user = db.relationship("User")


class Notification(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=True, index=True)
    title = db.Column(db.String(140), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    read_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False, index=True)

    user = db.relationship("User", foreign_keys=[user_id])
    ticket = db.relationship("Ticket", foreign_keys=[ticket_id])

    @property
    def is_unread(self):
        return self.read_at is None


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    entity = db.Column(db.String(100), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=True)
    action = db.Column(db.String(120), nullable=False)
    before = db.Column(db.JSON, nullable=True)
    after = db.Column(db.JSON, nullable=True)
    ip_address = db.Column(db.String(80), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False, index=True)

    user = db.relationship("User")


class SystemErrorLog(db.Model):
    __tablename__ = "system_error_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    user_name = db.Column(db.String(120), nullable=True)
    error_type = db.Column(db.String(160), nullable=False, index=True)
    error_message = db.Column(db.Text, nullable=False)
    endpoint = db.Column(db.String(160), nullable=True, index=True)
    path = db.Column(db.String(300), nullable=True, index=True)
    method = db.Column(db.String(12), nullable=True)
    status_code = db.Column(db.Integer, nullable=True, index=True)
    ip_address = db.Column(db.String(80), nullable=True)
    user_agent = db.Column(db.String(300), nullable=True)
    request_payload = db.Column(db.JSON, nullable=True)
    traceback = db.Column(db.Text, nullable=True)
    resolved = db.Column(db.Boolean, default=False, nullable=False, index=True)
    technical_note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False, index=True)

    user = db.relationship("User")


class BackupConfig(db.Model):
    __tablename__ = "backup_configs"

    id = db.Column(db.Integer, primary_key=True)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    schedule_times = db.Column(db.String(200), default="12:00,18:00", nullable=False)
    max_backup_count = db.Column(db.Integer, default=14, nullable=False)
    include_uploads = db.Column(db.Boolean, default=True, nullable=False)
    include_logs = db.Column(db.Boolean, default=True, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class BackupRun(db.Model):
    __tablename__ = "backup_runs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    action = db.Column(db.String(40), nullable=False, index=True)
    status = db.Column(db.String(40), nullable=False, default="running", index=True)
    file_name = db.Column(db.String(260), nullable=True)
    file_path = db.Column(db.String(500), nullable=True)
    size_bytes = db.Column(db.Integer, nullable=True)
    include_uploads = db.Column(db.Boolean, default=True, nullable=False)
    include_logs = db.Column(db.Boolean, default=True, nullable=False)
    message = db.Column(db.Text, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    finished_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User")
