from bloodbank import create_app, db
from datetime import timedelta

from bloodbank.models import BloodRequest, Inventory, PasswordResetToken, User, utc_now
from bloodbank.seed import seed_database
from bloodbank.services import (
    cleanup_password_reset_tokens,
    create_password_reset_token,
    get_valid_password_reset_token,
)


class TestConfig:
    SECRET_KEY = "test-secret"
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    PASSWORD_RESET_TOKEN_MINUTES = 30
    SHOW_DEV_RESET_LINK = True


def make_app():
    app = create_app(TestConfig)
    with app.app_context():
        seed_database()
    return app


def login(client, email, password):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )


def test_admin_login_loads_dashboard():
    app = make_app()
    with app.test_client() as client:
        response = login(client, "admin@lifedrop.local", "Admin@123")
        assert response.status_code == 200
        assert b"Blood bank command center" in response.data


def test_search_hides_ineligible_donors():
    app = make_app()
    with app.test_client() as client:
        login(client, "receiver@lifedrop.local", "Receiver@123")
        response = client.get("/donors/search?blood_group=B%2B&location=Delhi")
        assert response.status_code == 200
        assert b"Imran Khan" not in response.data


def test_emergency_request_broadcasts_to_matching_donors():
    app = make_app()
    with app.test_client() as client:
        login(client, "receiver@lifedrop.local", "Receiver@123")
        response = client.post(
            "/requests/new",
            data={
                "blood_group": "O+",
                "units": "1",
                "location": "Delhi",
                "hospital": "CityCare Hospital",
                "patient_name": "Test Patient",
                "contact_phone": "9444444444",
                "message": "Smoke test emergency.",
                "is_emergency": "1",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            created = BloodRequest.query.filter_by(patient_name="Test Patient").first()
            assert created is not None
            assert created.is_emergency is True
            assert created.broadcast_count > 0


def test_donor_donation_updates_inventory():
    app = make_app()
    with app.test_client() as client:
        with app.app_context():
            before = Inventory.query.filter_by(blood_group="O+").first().units
        login(client, "kabir.donor@lifedrop.local", "Donor@123")
        response = client.post(
            "/donations/record",
            data={"units": "1"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            after = Inventory.query.filter_by(blood_group="O+").first().units
            assert after == before + 1
            db.session.remove()


def test_invalid_form_input_is_handled_without_server_error():
    app = make_app()
    with app.test_client() as client:
        response = client.post(
            "/register",
            data={
                "role": "donor",
                "name": "Bad Date",
                "email": "bad-date@example.com",
                "password": "Secret1",
                "location": "Delhi",
                "blood_group": "O+",
                "phone": "9000000099",
                "last_donation_date": "not-a-date",
            },
        )
        assert response.status_code == 400
        assert b"YYYY-MM-DD" in response.data

        login(client, "receiver@lifedrop.local", "Receiver@123")
        response = client.post(
            "/requests/new",
            data={
                "blood_group": "BAD",
                "units": "abc",
                "location": "Delhi",
                "hospital": "QA Hospital",
                "patient_name": "QA Patient",
                "contact_phone": "9555555555",
            },
        )
        assert response.status_code == 200
        assert b"valid blood group" in response.data

        client.get("/logout")
        login(client, "admin@lifedrop.local", "Admin@123")
        response = client.post(
            "/inventory",
            data={"blood_group": "O+", "units": "abc", "low_stock_threshold": "3"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"whole number" in response.data


def test_large_accepted_request_can_be_completed():
    app = make_app()
    with app.test_client() as client:
        login(client, "receiver@lifedrop.local", "Receiver@123")
        response = client.post(
            "/requests/new",
            data={
                "blood_group": "A+",
                "units": "6",
                "location": "Delhi",
                "hospital": "Large Request Hospital",
                "patient_name": "Large Request Patient",
                "contact_phone": "9666666666",
                "message": "Needs multiple units.",
                "is_emergency": "1",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            request = BloodRequest.query.filter_by(patient_name="Large Request Patient").first()
            request_id = request.id

        client.get("/logout")
        login(client, "neha.donor@lifedrop.local", "Donor@123")
        response = client.post(f"/requests/{request_id}/accept", follow_redirects=True)
        assert response.status_code == 200
        assert b"accepted" in response.data.lower()

        client.get("/logout")
        login(client, "receiver@lifedrop.local", "Receiver@123")
        response = client.post(f"/requests/{request_id}/complete", follow_redirects=True)
        assert response.status_code == 200
        assert b"completed" in response.data.lower()

        with app.app_context():
            request = db.session.get(BloodRequest, request_id)
            assert request.status == "Completed"


def test_forgot_password_flow_resets_password_and_blocks_token_reuse():
    app = make_app()
    with app.test_client() as client:
        response = client.get("/login")
        assert response.status_code == 200
        assert b"Forgot password?" in response.data

        response = client.post(
            "/forgot-password",
            data={"email": "receiver@lifedrop.local"},
        )
        assert response.status_code == 200
        assert b"Local demo reset link" in response.data

        html = response.get_data(as_text=True)
        marker = "/reset-password/"
        assert marker in html
        token = html.split(marker, 1)[1].split('"', 1)[0]

        response = client.get(f"/reset-password/{token}")
        assert response.status_code == 200
        assert b"Set new password" in response.data

        response = client.post(
            f"/reset-password/{token}",
            data={"password": "NewPass@123", "confirm_password": "NewPass@123"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Password reset complete" in response.data

        old_login = login(client, "receiver@lifedrop.local", "Receiver@123")
        assert old_login.status_code == 401

        new_login = login(client, "receiver@lifedrop.local", "NewPass@123")
        assert new_login.status_code == 200
        assert b"Receiver dashboard" in new_login.data

        client.get("/logout")
        response = client.get(f"/reset-password/{token}", follow_redirects=True)
        assert response.status_code == 200
        assert b"invalid, expired, or already used" in response.data


def test_latest_reset_token_replaces_old_token_and_cleanup_removes_expired():
    app = make_app()
    with app.app_context():
        user = User.query.filter_by(email="receiver@lifedrop.local").first()
        old_token = create_password_reset_token(user, minutes=30)
        new_token = create_password_reset_token(user, minutes=30)

        assert get_valid_password_reset_token(old_token) is None
        assert get_valid_password_reset_token(new_token) is not None

        reset_token = get_valid_password_reset_token(new_token)
        reset_token.expires_at = utc_now() - timedelta(minutes=1)
        db.session.commit()

        assert get_valid_password_reset_token(new_token) is None
        deleted = cleanup_password_reset_tokens()
        assert deleted >= 2
        assert PasswordResetToken.query.count() == 0
