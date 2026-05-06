import json
import smtplib
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from bloodbank import db
from bloodbank.models import BLOOD_GROUPS, BloodRequest, Donor, Inventory, User, utc_now
from bloodbank.services import (
    accept_request,
    admin_chart_payload,
    admin_metrics,
    complete_request,
    consume_password_reset_token,
    create_password_reset_token,
    create_blood_request,
    donor_eligibility,
    donor_score,
    get_valid_password_reset_token,
    low_stock_items,
    normalize_email,
    normalize_text,
    parse_date,
    parse_int_field,
    ranked_donors,
    record_donation,
    send_password_reset_email,
    validate_blood_group,
)

bp = Blueprint("main", __name__)


def role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view_func(*args, **kwargs)

        return wrapper

    return decorator


@bp.app_context_processor
def inject_globals():
    return {
        "blood_groups": BLOOD_GROUPS,
        "donor_score": donor_score,
        "donor_eligibility": donor_eligibility,
    }


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("main.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        email = normalize_email(request.form.get("email"))
        password = request.form.get("password") or ""
        user = User.query.filter_by(email=email).first()
        if user is None or not user.check_password(password):
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html"), 401
        if not user.is_active_account:
            flash("This account is inactive. Contact the administrator.", "danger")
            return render_template("auth/login.html"), 403

        user.last_login_at = utc_now()
        if user.donor_profile:
            user.donor_profile.last_active_at = utc_now()
        db.session.commit()
        login_user(user)
        flash(f"Welcome back, {user.name}.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("auth/login.html")


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    reset_url = None
    email_sent = False
    if request.method == "POST":
        email = normalize_email(request.form.get("email"))
        user = User.query.filter_by(email=email).first()
        if user and user.is_active_account:
            raw_token = create_password_reset_token(
                user,
                minutes=current_app.config["PASSWORD_RESET_TOKEN_MINUTES"],
            )
            reset_url = url_for("main.reset_password", token=raw_token, _external=True)
            try:
                email_sent = send_password_reset_email(current_app, user, reset_url)
            except (OSError, smtplib.SMTPException):
                email_sent = False

        return render_template(
            "auth/forgot_sent.html",
            email=email,
            email_sent=email_sent,
            reset_url=reset_url if current_app.config["SHOW_DEV_RESET_LINK"] else None,
            token_minutes=current_app.config["PASSWORD_RESET_TOKEN_MINUTES"],
        )

    return render_template("auth/forgot_password.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    if current_user.is_authenticated:
        logout_user()

    reset_token = get_valid_password_reset_token(token)
    if reset_token is None:
        flash("This reset link is invalid, expired, or already used.", "danger")
        return redirect(url_for("main.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password") or ""
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "danger")
            return render_template("auth/reset_password.html", token=token), 400
        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("auth/reset_password.html", token=token), 400

        consume_password_reset_token(reset_token, password)
        flash("Password reset complete. Login with your new password.", "success")
        return redirect(url_for("main.login"))

    return render_template("auth/reset_password.html", token=token)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        role = request.form.get("role")
        email = normalize_email(request.form.get("email"))
        password = request.form.get("password") or ""
        name = normalize_text(request.form.get("name"))
        location = normalize_text(request.form.get("location"))

        if role not in ("donor", "receiver"):
            flash("Choose donor or receiver registration.", "danger")
            return render_template("auth/register.html"), 400
        if not name or not email or len(password) < 6 or not location:
            flash("Name, email, location, and a 6 character password are required.", "danger")
            return render_template("auth/register.html"), 400
        if User.query.filter_by(email=email).first():
            flash("An account with this email already exists.", "danger")
            return render_template("auth/register.html"), 409

        donor_data = None
        if role == "donor":
            try:
                phone = normalize_text(request.form.get("phone"))
                if not phone:
                    raise ValueError("Donor phone number is required.")
                donor_data = {
                    "blood_group": validate_blood_group(request.form.get("blood_group")),
                    "phone": phone,
                    "city": normalize_text(request.form.get("city")) or location,
                    "area": normalize_text(request.form.get("area")) or location,
                    "last_donation_date": parse_date(request.form.get("last_donation_date")),
                    "is_available": request.form.get("is_available") == "on",
                }
            except ValueError as exc:
                flash(str(exc), "danger")
                return render_template("auth/register.html"), 400

        user = User(name=name, email=email, role=role, location=location)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()

        if donor_data:
            db.session.add(Donor(user=user, **donor_data))

        db.session.commit()
        flash("Registration complete. Log in to continue.", "success")
        return redirect(url_for("main.login"))

    return render_template("auth/register.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.login"))


@bp.route("/dashboard")
@login_required
def dashboard():
    if current_user.role == "admin":
        return redirect(url_for("main.admin_dashboard"))
    if current_user.role == "donor":
        return redirect(url_for("main.donor_dashboard"))
    return redirect(url_for("main.receiver_dashboard"))


@bp.route("/dashboard/donor")
@role_required("donor")
def donor_dashboard():
    donor = current_user.donor_profile
    if donor is None:
        abort(404)

    notifications = (
        donor.notifications
        and sorted(
            [
                item
                for item in donor.notifications
                if item.status == "Sent" and item.request.status == "Pending"
            ],
            key=lambda item: (item.request.priority, item.request.created_at),
            reverse=True,
        )
    ) or []
    accepted_requests = (
        BloodRequest.query.filter_by(donor_id=donor.id)
        .order_by(BloodRequest.created_at.desc())
        .limit(6)
        .all()
    )
    inventory_item = Inventory.query.filter_by(blood_group=donor.blood_group).first()

    return render_template(
        "dashboards/donor.html",
        donor=donor,
        eligibility=donor_eligibility(donor),
        score=donor_score(donor),
        notifications=notifications,
        accepted_requests=accepted_requests,
        inventory_item=inventory_item,
    )


@bp.route("/dashboard/receiver")
@role_required("receiver")
def receiver_dashboard():
    requests = (
        BloodRequest.query.filter_by(receiver_id=current_user.id)
        .order_by(BloodRequest.priority.desc(), BloodRequest.created_at.desc())
        .all()
    )
    pending_count = sum(1 for item in requests if item.status == "Pending")
    completed_count = sum(1 for item in requests if item.status == "Completed")
    return render_template(
        "dashboards/receiver.html",
        requests=requests,
        pending_count=pending_count,
        completed_count=completed_count,
    )


@bp.route("/dashboard/admin")
@role_required("admin")
def admin_dashboard():
    recent_requests = (
        BloodRequest.query.order_by(BloodRequest.priority.desc(), BloodRequest.created_at.desc())
        .limit(8)
        .all()
    )
    donors = Donor.query.join(User).order_by(Donor.last_active_at.desc()).limit(8).all()
    inventory = Inventory.query.order_by(Inventory.blood_group).all()
    return render_template(
        "dashboards/admin.html",
        metrics=admin_metrics(),
        chart_payload=json.dumps(admin_chart_payload()),
        low_stock=low_stock_items(),
        recent_requests=recent_requests,
        donors=donors,
        inventory=inventory,
    )


@bp.route("/donors/search")
@role_required("receiver", "admin")
def search_donors():
    blood_group = request.args.get("blood_group") or ""
    location = request.args.get("location") or ""
    results = []
    if blood_group or location:
        results = ranked_donors(
            blood_group=blood_group or None,
            location=location or None,
            hide_ineligible=True,
        )
    return render_template(
        "donors/search.html",
        results=results,
        selected_blood_group=blood_group,
        selected_location=location,
    )


@bp.route("/requests/new", methods=["GET", "POST"])
@role_required("receiver")
def new_request():
    if request.method == "POST":
        try:
            blood_request = create_blood_request(request.form, current_user)
            label = "Emergency request" if blood_request.is_emergency else "Blood request"
            flash(
                f"{label} created and broadcast to {blood_request.broadcast_count} matching donors.",
                "success",
            )
            return redirect(url_for("main.receiver_dashboard"))
        except (KeyError, ValueError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")

    return render_template("requests/new.html")


@bp.route("/requests/<int:request_id>/accept", methods=["POST"])
@role_required("donor")
def accept_blood_request(request_id):
    blood_request = db.session.get(BloodRequest, request_id) or abort(404)
    try:
        accept_request(blood_request, current_user.donor_profile)
        flash("Request accepted. Coordinate with the receiver using the contact details.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("main.donor_dashboard"))


@bp.route("/requests/<int:request_id>/complete", methods=["POST"])
@role_required("receiver", "admin")
def complete_blood_request(request_id):
    blood_request = db.session.get(BloodRequest, request_id) or abort(404)
    if current_user.role != "admin" and blood_request.receiver_id != current_user.id:
        abort(403)
    try:
        complete_request(blood_request)
        flash("Request completed and inventory updated.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")

    if current_user.role == "admin":
        return redirect(url_for("main.admin_dashboard"))
    return redirect(url_for("main.receiver_dashboard"))


@bp.route("/requests/<int:request_id>/cancel", methods=["POST"])
@role_required("receiver", "admin")
def cancel_blood_request(request_id):
    blood_request = db.session.get(BloodRequest, request_id) or abort(404)
    if current_user.role != "admin" and blood_request.receiver_id != current_user.id:
        abort(403)
    if blood_request.status == "Completed":
        flash("Completed requests cannot be cancelled.", "danger")
    else:
        blood_request.status = "Cancelled"
        db.session.commit()
        flash("Request cancelled.", "info")

    if current_user.role == "admin":
        return redirect(url_for("main.admin_dashboard"))
    return redirect(url_for("main.receiver_dashboard"))


@bp.route("/donations/record", methods=["POST"])
@role_required("donor")
def donor_record_donation():
    donor = current_user.donor_profile
    if not donor_eligibility(donor)["eligible"]:
        flash("You are not eligible to donate yet.", "danger")
        return redirect(url_for("main.donor_dashboard"))

    try:
        units = parse_int_field(request.form.get("units"), "Units", default=1, minimum=1, maximum=4)
        record_donation(donor, units=units, notes="Self-recorded blood bank donation.")
        flash("Donation recorded. Inventory and eligibility have been updated.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("main.donor_dashboard"))


@bp.route("/inventory", methods=["GET", "POST"])
@role_required("admin")
def inventory():
    if request.method == "POST":
        try:
            blood_group = validate_blood_group(request.form.get("blood_group"))
            units = parse_int_field(request.form.get("units"), "Units", default=0, minimum=0)
            threshold = parse_int_field(
                request.form.get("low_stock_threshold"),
                "Low stock threshold",
                default=5,
                minimum=1,
            )
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("main.inventory"))

        inventory_item = Inventory.query.filter_by(blood_group=blood_group).first()
        if inventory_item is None:
            flash("Invalid blood group.", "danger")
            return redirect(url_for("main.inventory"))

        inventory_item.units = units
        inventory_item.low_stock_threshold = threshold
        db.session.commit()
        flash(f"{blood_group} inventory updated.", "success")
        return redirect(url_for("main.inventory"))

    inventory_items = Inventory.query.order_by(Inventory.blood_group).all()
    return render_template(
        "inventory/index.html",
        inventory_items=inventory_items,
        low_stock=low_stock_items(),
    )


@bp.route("/admin/donors/<int:donor_id>/toggle", methods=["POST"])
@role_required("admin")
def toggle_donor(donor_id):
    donor = db.session.get(Donor, donor_id) or abort(404)
    donor.user.is_active_account = not donor.user.is_active_account
    if not donor.user.is_active_account:
        donor.is_available = False
    db.session.commit()
    flash(f"{donor.user.name} account updated.", "success")
    return redirect(url_for("main.admin_dashboard"))


@bp.errorhandler(403)
def forbidden(_error):
    return render_template("errors.html", code=403, title="Access denied"), 403


@bp.errorhandler(404)
def not_found(_error):
    return render_template("errors.html", code=404, title="Page not found"), 404
