from datetime import date, datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from bloodbank import db

BLOOD_GROUPS = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")
USER_ROLES = ("donor", "receiver", "admin")
REQUEST_STATUSES = ("Pending", "Accepted", "Completed", "Cancelled")
NOTIFICATION_STATUSES = ("Sent", "Read", "Accepted", "Skipped")


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, index=True)
    location = db.Column(db.String(120), nullable=False)
    is_active_account = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    last_login_at = db.Column(db.DateTime(timezone=True))

    donor_profile = db.relationship(
        "Donor",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    receiver_requests = db.relationship(
        "BloodRequest",
        back_populates="receiver",
        foreign_keys="BloodRequest.receiver_id",
    )
    password_reset_tokens = db.relationship(
        "PasswordResetToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    @property
    def is_active(self):
        return self.is_active_account

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    used_at = db.Column(db.DateTime(timezone=True))

    user = db.relationship("User", back_populates="password_reset_tokens")

    @property
    def is_used(self):
        return self.used_at is not None

    @property
    def is_expired(self):
        return utc_now() > self.expires_at


class Donor(db.Model):
    __tablename__ = "donors"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    blood_group = db.Column(db.String(4), nullable=False, index=True)
    phone = db.Column(db.String(30), nullable=False)
    city = db.Column(db.String(120), nullable=False, index=True)
    area = db.Column(db.String(120), nullable=False)
    last_donation_date = db.Column(db.Date)
    is_available = db.Column(db.Boolean, nullable=False, default=True, index=True)
    total_donations = db.Column(db.Integer, nullable=False, default=0)
    last_active_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    medical_notes = db.Column(db.Text)

    user = db.relationship("User", back_populates="donor_profile")
    notifications = db.relationship(
        "RequestNotification",
        back_populates="donor",
        cascade="all, delete-orphan",
    )
    accepted_requests = db.relationship(
        "BloodRequest",
        back_populates="donor",
        foreign_keys="BloodRequest.donor_id",
    )
    donations = db.relationship("Donation", back_populates="donor")


class BloodRequest(db.Model):
    __tablename__ = "requests"

    id = db.Column(db.Integer, primary_key=True)
    receiver_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    donor_id = db.Column(db.Integer, db.ForeignKey("donors.id"), index=True)
    blood_group = db.Column(db.String(4), nullable=False, index=True)
    location = db.Column(db.String(120), nullable=False, index=True)
    hospital = db.Column(db.String(160), nullable=False)
    patient_name = db.Column(db.String(120), nullable=False)
    contact_phone = db.Column(db.String(30), nullable=False)
    units = db.Column(db.Integer, nullable=False, default=1)
    is_emergency = db.Column(db.Boolean, nullable=False, default=False, index=True)
    priority = db.Column(db.Integer, nullable=False, default=10, index=True)
    status = db.Column(db.String(20), nullable=False, default="Pending", index=True)
    message = db.Column(db.Text)
    broadcast_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    accepted_at = db.Column(db.DateTime(timezone=True))
    completed_at = db.Column(db.DateTime(timezone=True))

    receiver = db.relationship(
        "User",
        back_populates="receiver_requests",
        foreign_keys=[receiver_id],
    )
    donor = db.relationship(
        "Donor",
        back_populates="accepted_requests",
        foreign_keys=[donor_id],
    )
    notifications = db.relationship(
        "RequestNotification",
        back_populates="request",
        cascade="all, delete-orphan",
    )
    donations = db.relationship("Donation", back_populates="request")


class Inventory(db.Model):
    __tablename__ = "inventory"

    id = db.Column(db.Integer, primary_key=True)
    blood_group = db.Column(db.String(4), nullable=False, unique=True, index=True)
    units = db.Column(db.Integer, nullable=False, default=0)
    low_stock_threshold = db.Column(db.Integer, nullable=False, default=5)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )


class RequestNotification(db.Model):
    __tablename__ = "request_notifications"

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey("requests.id"), nullable=False, index=True)
    donor_id = db.Column(db.Integer, db.ForeignKey("donors.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="Sent", index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    responded_at = db.Column(db.DateTime(timezone=True))

    request = db.relationship("BloodRequest", back_populates="notifications")
    donor = db.relationship("Donor", back_populates="notifications")


class Donation(db.Model):
    __tablename__ = "donations"

    id = db.Column(db.Integer, primary_key=True)
    donor_id = db.Column(db.Integer, db.ForeignKey("donors.id"), nullable=False, index=True)
    request_id = db.Column(db.Integer, db.ForeignKey("requests.id"), index=True)
    blood_group = db.Column(db.String(4), nullable=False, index=True)
    units = db.Column(db.Integer, nullable=False, default=1)
    donation_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    notes = db.Column(db.String(255))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    donor = db.relationship("Donor", back_populates="donations")
    request = db.relationship("BloodRequest", back_populates="donations")
