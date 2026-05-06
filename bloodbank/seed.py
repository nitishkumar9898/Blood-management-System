from datetime import date, timedelta

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
from bloodbank.services import (
    adjust_inventory,
    broadcast_request,
    cleanup_password_reset_tokens,
    ensure_inventory_rows,
)


def register_seed_commands(app):
    @app.cli.command("seed")
    def seed_command():
        seed_database()
        print("Seeded LifeDrop demo data.")

    @app.cli.command("cleanup-reset-tokens")
    def cleanup_reset_tokens_command():
        deleted = cleanup_password_reset_tokens()
        print(f"Deleted {deleted} expired or used password reset token(s).")


def make_user(name, email, role, location, password):
    user = User(name=name, email=email, role=role, location=location)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    return user


def make_donor(
    name,
    email,
    blood_group,
    city,
    area,
    phone,
    last_donation_days,
    total_donations,
    available=True,
    active=True,
):
    user = make_user(name, email, "donor", city, "Donor@123")
    user.is_active_account = active
    last_donation_date = None
    if last_donation_days is not None:
        last_donation_date = date.today() - timedelta(days=last_donation_days)
    donor = Donor(
        user=user,
        blood_group=blood_group,
        phone=phone,
        city=city,
        area=area,
        last_donation_date=last_donation_date,
        is_available=available,
        total_donations=total_donations,
        last_active_at=utc_now() - timedelta(days=min(last_donation_days or 8, 45)),
    )
    db.session.add(donor)
    db.session.flush()
    return donor


def seed_database():
    PasswordResetToken.query.delete()
    RequestNotification.query.delete()
    Donation.query.delete()
    BloodRequest.query.delete()
    Donor.query.delete()
    User.query.delete()
    Inventory.query.delete()
    db.session.commit()

    ensure_inventory_rows()

    admin = make_user("Admin Manager", "admin@lifedrop.local", "admin", "Delhi", "Admin@123")
    receiver = make_user("Riya Sharma", "receiver@lifedrop.local", "receiver", "Delhi", "Receiver@123")
    receiver_two = make_user("Aman Verma", "aman.receiver@lifedrop.local", "receiver", "Noida", "Receiver@123")

    donors = [
        make_donor("Kabir Singh", "kabir.donor@lifedrop.local", "O+", "Delhi", "Karol Bagh", "9000000001", 128, 7),
        make_donor("Neha Kapoor", "neha.donor@lifedrop.local", "A+", "Delhi", "Saket", "9000000002", 96, 5),
        make_donor("Imran Khan", "imran.donor@lifedrop.local", "B+", "Delhi", "Dwarka", "9000000003", 45, 4),
        make_donor("Sara Thomas", "sara.donor@lifedrop.local", "AB+", "Noida", "Sector 62", "9000000004", 160, 9),
        make_donor("Arjun Mehta", "arjun.donor@lifedrop.local", "O-", "Gurugram", "Cyber City", "9000000005", None, 0),
        make_donor("Pooja Nair", "pooja.donor@lifedrop.local", "B-", "Delhi", "Rohini", "9000000006", 110, 6, available=False),
        make_donor("Vikram Rao", "vikram.donor@lifedrop.local", "A-", "Noida", "Sector 18", "9000000007", 130, 3, active=False),
        make_donor("Meera Iyer", "meera.donor@lifedrop.local", "AB-", "Delhi", "Lajpat Nagar", "9000000008", 94, 8),
    ]

    starting_stock = {
        "A+": 9,
        "A-": 3,
        "B+": 12,
        "B-": 2,
        "AB+": 5,
        "AB-": 4,
        "O+": 14,
        "O-": 2,
    }
    for group in BLOOD_GROUPS:
        item = Inventory.query.filter_by(blood_group=group).first()
        item.units = starting_stock[group]
        item.low_stock_threshold = 4 if group in ("O-", "B-", "A-") else 5

    today = date.today()
    donation_rows = [
        (donors[0], 2, today - timedelta(days=18)),
        (donors[1], 1, today - timedelta(days=38)),
        (donors[3], 2, today - timedelta(days=70)),
        (donors[7], 1, today - timedelta(days=96)),
        (donors[4], 1, today - timedelta(days=120)),
    ]
    for donor, units, donation_date in donation_rows:
        db.session.add(
            Donation(
                donor=donor,
                blood_group=donor.blood_group,
                units=units,
                donation_date=donation_date,
                notes="Seeded historical donation.",
            )
        )

    emergency = BloodRequest(
        receiver=receiver,
        blood_group="O+",
        location="Delhi",
        hospital="CityCare Hospital",
        patient_name="Mahesh Sharma",
        contact_phone="9111111111",
        units=2,
        is_emergency=True,
        priority=100,
        status="Pending",
        message="Urgent surgery support required.",
    )
    accepted = BloodRequest(
        receiver=receiver_two,
        donor=donors[3],
        blood_group="AB+",
        location="Noida",
        hospital="Metro Hospital",
        patient_name="Dev Malhotra",
        contact_phone="9222222222",
        units=1,
        is_emergency=False,
        priority=10,
        status="Accepted",
        accepted_at=utc_now(),
        broadcast_count=1,
    )
    completed = BloodRequest(
        receiver=receiver,
        donor=donors[1],
        blood_group="A+",
        location="Delhi",
        hospital="Apollo Clinic",
        patient_name="Kavya Sharma",
        contact_phone="9333333333",
        units=1,
        is_emergency=False,
        priority=10,
        status="Completed",
        accepted_at=utc_now(),
        completed_at=utc_now(),
        broadcast_count=1,
    )
    db.session.add_all([emergency, accepted, completed])
    db.session.flush()

    broadcast_request(emergency)
    db.session.add(RequestNotification(request=accepted, donor=donors[3], status="Accepted", responded_at=utc_now()))
    db.session.add(RequestNotification(request=completed, donor=donors[1], status="Accepted", responded_at=utc_now()))

    adjust_inventory("A+", -1)
    db.session.commit()

    _ = admin
