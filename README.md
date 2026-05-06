# LifeDrop

LifeDrop is a Flask based Blood Donor and Blood Bank Management System with role based login, donor eligibility scoring, emergency request broadcasts, inventory tracking, and admin analytics.

## Stack

- Flask
- Flask-SQLAlchemy
- Flask-Login
- SQLite
- HTML, CSS, JavaScript canvas charts

## Demo Accounts

| Role | Email | Password |
| --- | --- | --- |
| Admin | admin@lifedrop.local | Admin@123 |
| Receiver | receiver@lifedrop.local | Receiver@123 |
| Donor | kabir.donor@lifedrop.local | Donor@123 |

## Forgot Password

Open `Forgot password?` on the login page, enter a registered email, and use the generated reset link.

For local demos, LifeDrop shows the reset link on screen when SMTP is not configured. For real email delivery, set:

```powershell
$env:MAIL_SERVER="smtp.example.com"
$env:MAIL_PORT="587"
$env:MAIL_USERNAME="your-user"
$env:MAIL_PASSWORD="your-password"
$env:MAIL_DEFAULT_SENDER="noreply@example.com"
```

Clean expired or used reset tokens:

```powershell
python -m flask --app app cleanup-reset-tokens
```

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
flask --app app seed
flask --app app run
```

Open `http://127.0.0.1:5000`.

## Test

```powershell
python -m pytest
```
