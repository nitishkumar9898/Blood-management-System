import hashlib
import secrets
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import func

from bloodbank import db
from bloodbank.models import (
    BLOOD_GROUPS,
    BloodRequest,
    Donation,
    Donor,
    Inventory,
    PasswordResetToken,
    RequestNotification,
    User,
    utc_now,
)

ELIGIBILITY_DAYS = 90
RECENT_ACTIVITY_DAYS = 30
PASSWORD_RESET_TOKEN_BYTES = 32


def ensure_inventory_rows():
    for group in BLOOD_GROUPS:
        existing = Inventory.query.filter_by(blood_group=group).first()
        if existing is None:
            db.session.add(Inventory(blood_group=group, units=0, low_stock_threshold=5))
    db.session.commit()


def normalize_text(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize_email(value):
    return normalize_text(value).lower()


def parse_date(value):
    value = normalize_text(value)
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("Date must use YYYY-MM-DD format.") from exc


def parse_int_field(value, field_name, default=None, minimum=None, maximum=None):
    value = normalize_text(value)
    if value == "":
        if default is None:
            raise ValueError(f"{field_name} is required.")
        number = default
    else:
        try:
            number = int(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a whole number.") from exc

    if minimum is not None and number < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}.")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field_name} must be at most {maximum}.")
    return number


def validate_blood_group(value):
    blood_group = normalize_text(value).upper()
    if blood_group not in BLOOD_GROUPS:
        raise ValueError("Choose a valid blood group.")
    return blood_group


def hash_reset_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_password_reset_token(user, minutes=30):
    PasswordResetToken.query.filter_by(user_id=user.id, used_at=None).update(
        {"used_at": utc_now()},
    )
    raw_token = secrets.token_urlsafe(PASSWORD_RESET_TOKEN_BYTES)
    reset_token = PasswordResetToken(
        user=user,
        token_hash=hash_reset_token(raw_token),
        expires_at=utc_now() + timedelta(minutes=minutes),
    )
    db.session.add(reset_token)
    db.session.commit()
    return raw_token


def get_valid_password_reset_token(raw_token):
    token_hash = hash_reset_token(normalize_text(raw_token))
    reset_token = PasswordResetToken.query.filter_by(token_hash=token_hash).first()
    if reset_token is None or reset_token.is_used or reset_token.is_expired:
        return None
    if not reset_token.user.is_active_account:
        return None
    return reset_token


def consume_password_reset_token(reset_token, new_password):
    reset_token.user.set_password(new_password)
    reset_token.used_at = utc_now()
    PasswordResetToken.query.filter(
        PasswordResetToken.user_id == reset_token.user_id,
        PasswordResetToken.id != reset_token.id,
        PasswordResetToken.used_at.is_(None),
    ).update({"used_at": utc_now()})
    db.session.commit()


def cleanup_password_reset_tokens():
    deleted = PasswordResetToken.query.filter(
        db.or_(
            PasswordResetToken.used_at.is_not(None),
            PasswordResetToken.expires_at < utc_now(),
        )
    ).delete(synchronize_session=False)
    db.session.commit()
    return deleted


def send_password_reset_email(app, user, reset_url):
    mail_server = app.config.get("MAIL_SERVER")
    sender = app.config.get("MAIL_DEFAULT_SENDER")
    if not mail_server or not sender:
        return False

    message = EmailMessage()
    message["Subject"] = "LifeDrop password reset"
    message["From"] = sender
    message["To"] = user.email
    message.set_content(
        "Hello {name},\n\n"
        "Use this link to reset your LifeDrop password. The link expires soon and works only once:\n"
        "{url}\n\n"
        "If you did not request this, ignore this email.\n".format(
            name=user.name,
            url=reset_url,
        )
    )

    smtp = smtplib.SMTP(app.config["MAIL_SERVER"], app.config["MAIL_PORT"], timeout=10)
    try:
        if app.config.get("MAIL_USE_TLS"):
            smtp.starttls()
        username = app.config.get("MAIL_USERNAME")
        password = app.config.get("MAIL_PASSWORD")
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message)
    finally:
        smtp.quit()
    return True


def donor_days_since_last_donation(donor, on_date=None):
    if donor.last_donation_date is None:
        return None
    on_date = on_date or date.today()
    return (on_date - donor.last_donation_date).days


def donor_eligibility(donor, on_date=None):
    on_date = on_date or date.today()
    days_since = donor_days_since_last_donation(donor, on_date)
    if days_since is None:
        return {
            "eligible": True,
            "days_since": None,
            "remaining_days": 0,
            "next_date": on_date,
            "label": "Eligible",
        }

    eligible = days_since >= ELIGIBILITY_DAYS
    remaining_days = max(0, ELIGIBILITY_DAYS - days_since)
    next_date = donor.last_donation_date + timedelta(days=ELIGIBILITY_DAYS)
    return {
        "eligible": eligible,
        "days_since": days_since,
        "remaining_days": remaining_days,
        "next_date": next_date,
        "label": "Eligible" if eligible else f"{remaining_days} days left",
    }


def recent_activity_value(donor):
    if donor.last_active_at is None:
        return 0
    cutoff = utc_now() - timedelta(days=RECENT_ACTIVITY_DAYS)
    return 1 if donor.last_active_at >= cutoff else 0


def donor_score(donor):
    availability = 1 if donor.is_available and donor.user.is_active_account else 0
    recent_activity = recent_activity_value(donor)
    eligibility = 1 if donor_eligibility(donor)["eligible"] else 0
    return (availability * 5) + (recent_activity * 3) + (eligibility * 10)


def ranked_donors(blood_group=None, location=None, hide_ineligible=True):
    query = Donor.query.join(User).filter(User.is_active_account.is_(True))
    if blood_group:
        query = query.filter(Donor.blood_group == blood_group)
    if location:
        location_like = f"%{location.strip()}%"
        query = query.filter(
            db.or_(
                Donor.city.ilike(location_like),
                Donor.area.ilike(location_like),
                User.location.ilike(location_like),
            )
        )

    donors = query.all()
    ranked = []
    for donor in donors:
        eligibility = donor_eligibility(donor)
        score = donor_score(donor)
        if hide_ineligible and (not eligibility["eligible"] or not donor.is_available):
            continue
        ranked.append({"donor": donor, "eligibility": eligibility, "score": score})

    ranked.sort(
        key=lambda item: (
            item["score"],
            item["donor"].total_donations,
            item["donor"].last_active_at or utc_now(),
        ),
        reverse=True,
    )
    return ranked


def broadcast_request(blood_request):
    same_location = ranked_donors(
        blood_group=blood_request.blood_group,
        location=blood_request.location,
        hide_ineligible=True,
    )
    candidates = same_location

    if blood_request.is_emergency and not candidates:
        candidates = ranked_donors(
            blood_group=blood_request.blood_group,
            location=None,
            hide_ineligible=True,
        )

    created = 0
    for item in candidates:
        donor = item["donor"]
        exists = RequestNotification.query.filter_by(
            request_id=blood_request.id,
            donor_id=donor.id,
        ).first()
        if exists is None:
            db.session.add(RequestNotification(request=blood_request, donor=donor))
            created += 1

    blood_request.broadcast_count += created
    db.session.commit()
    return created


def create_blood_request(form_data, receiver):
    is_emergency = form_data.get("is_emergency") in ("1", "true", "on", True)
    blood_group = validate_blood_group(form_data.get("blood_group"))
    location = normalize_text(form_data.get("location"))
    hospital = normalize_text(form_data.get("hospital"))
    patient_name = normalize_text(form_data.get("patient_name"))
    contact_phone = normalize_text(form_data.get("contact_phone"))
    units = parse_int_field(form_data.get("units"), "Units", default=1, minimum=1, maximum=6)

    if not location or not hospital or not patient_name or not contact_phone:
        raise ValueError("Location, hospital, patient, and contact are required.")

    blood_request = BloodRequest(
        receiver=receiver,
        blood_group=blood_group,
        location=location,
        hospital=hospital,
        patient_name=patient_name,
        contact_phone=contact_phone,
        units=units,
        is_emergency=is_emergency,
        priority=100 if is_emergency else 10,
        message=normalize_text(form_data.get("message")),
    )
    db.session.add(blood_request)
    db.session.commit()
    broadcast_request(blood_request)
    return blood_request


def accept_request(blood_request, donor):
    if blood_request.status != "Pending":
        raise ValueError("Only pending requests can be accepted.")
    if blood_request.blood_group != donor.blood_group:
        raise ValueError("Blood group does not match this request.")
    if not donor.is_available or not donor.user.is_active_account:
        raise ValueError("This donor is currently unavailable.")
    if not donor_eligibility(donor)["eligible"]:
        raise ValueError("This donor is not eligible yet.")

    blood_request.status = "Accepted"
    blood_request.donor = donor
    blood_request.accepted_at = utc_now()
    donor.last_active_at = utc_now()

    notification = RequestNotification.query.filter_by(
        request_id=blood_request.id,
        donor_id=donor.id,
    ).first()
    if notification:
        notification.status = "Accepted"
        notification.responded_at = utc_now()

    RequestNotification.query.filter(
        RequestNotification.request_id == blood_request.id,
        RequestNotification.donor_id != donor.id,
        RequestNotification.status == "Sent",
    ).update({"status": "Skipped"})
    db.session.commit()


def get_inventory(blood_group):
    inventory = Inventory.query.filter_by(blood_group=blood_group).first()
    if inventory is None:
        inventory = Inventory(blood_group=blood_group, units=0, low_stock_threshold=5)
        db.session.add(inventory)
        db.session.flush()
    return inventory


def adjust_inventory(blood_group, delta_units):
    inventory = get_inventory(blood_group)
    new_units = inventory.units + delta_units
    if new_units < 0:
        raise ValueError(f"Not enough {blood_group} inventory.")
    inventory.units = new_units
    inventory.updated_at = utc_now()
    db.session.flush()
    return inventory


def record_donation(donor, units=1, request=None, notes=None, commit=True, maximum_units=4):
    units = parse_int_field(units, "Units", default=1, minimum=1, maximum=maximum_units)
    donation = Donation(
        donor=donor,
        request=request,
        blood_group=donor.blood_group,
        units=units,
        donation_date=date.today(),
        notes=notes,
    )
    db.session.add(donation)
    donor.last_donation_date = date.today()
    donor.last_active_at = utc_now()
    donor.total_donations += units
    adjust_inventory(donor.blood_group, units)
    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return donation


def complete_request(blood_request):
    if blood_request.status not in ("Pending", "Accepted"):
        raise ValueError("Only pending or accepted requests can be completed.")

    if blood_request.donor:
        record_donation(
            blood_request.donor,
            units=blood_request.units,
            request=blood_request,
            notes="Donation linked to request completion.",
            commit=False,
            maximum_units=6,
        )

    adjust_inventory(blood_request.blood_group, -blood_request.units)
    blood_request.status = "Completed"
    blood_request.completed_at = utc_now()
    db.session.commit()


def low_stock_items():
    return Inventory.query.filter(Inventory.units <= Inventory.low_stock_threshold).all()


def admin_metrics():
    total_donors = Donor.query.count()
    active_donors = (
        Donor.query.join(User)
        .filter(User.is_active_account.is_(True), Donor.is_available.is_(True))
        .count()
    )
    total_requests = BloodRequest.query.count()
    completed_requests = BloodRequest.query.filter_by(status="Completed").count()
    total_stock = db.session.query(func.coalesce(func.sum(Inventory.units), 0)).scalar()
    success_rate = round((completed_requests / total_requests) * 100, 1) if total_requests else 0
    return {
        "total_donors": total_donors,
        "active_donors": active_donors,
        "total_requests": total_requests,
        "completed_requests": completed_requests,
        "total_stock": total_stock,
        "success_rate": success_rate,
    }


def admin_chart_payload():
    monthly_rows = (
        db.session.query(
            func.strftime("%Y-%m", Donation.donation_date).label("month"),
            func.coalesce(func.sum(Donation.units), 0).label("units"),
        )
        .group_by("month")
        .order_by("month")
        .all()
    )
    inventory_rows = Inventory.query.order_by(Inventory.blood_group).all()
    status_rows = (
        db.session.query(BloodRequest.status, func.count(BloodRequest.id))
        .group_by(BloodRequest.status)
        .all()
    )

    return {
        "monthlyDonations": {
            "labels": [row.month for row in monthly_rows],
            "values": [int(row.units) for row in monthly_rows],
        },
        "bloodDistribution": {
            "labels": [item.blood_group for item in inventory_rows],
            "values": [item.units for item in inventory_rows],
        },
        "requestStatus": {
            "labels": [row[0] for row in status_rows],
            "values": [row[1] for row in status_rows],
        },
    }
