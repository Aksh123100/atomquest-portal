---
title: AtomQuest Portal
emoji: 🚀
colorFrom: blue
colorTo: purple
sdk: docker
pinned: false
---

# AtomQuest Portal

AtomQuest Portal is a role-based KPI and quarterly performance tracking system for **employees**, **managers**, and **admins**.

It supports end-to-end goal lifecycle management: goal drafting, approval workflow, check-ins, completion visibility, audit trail, escalations, and queued email notifications.

## Key Features

- **Role-based access** (employee / manager / admin) with JWT cookie auth.
- **Goal setting workflow** with constraints (max goals, weightage rules, submission/approval flow).
- **Manager review flow** for approvals, rework comments, and quarter check-in comments.
- **Employee check-ins** with achievement scoring across quarters.
- **Admin controls**:
  - completion dashboard
  - analytics dashboard
  - escalation center
  - email queue dispatch
  - audit trail
  - check-in window toggles
  - CSV export
- **Escalation engine** for missed check-ins / missed manager reviews.
- **Notification queue** with SMTP-backed send flow.

## Tech Stack

- **Backend:** FastAPI
- **ORM/DB:** SQLAlchemy + SQLite/PostgreSQL
- **Frontend:** Jinja2 templates + Tailwind CDN
- **Auth:** JWT + passlib bcrypt
- **Deploy:** Docker (Hugging Face Spaces compatible)

## Project Structure

```text
app/
  main.py
  auth.py
  database.py
  models.py
  routes/
    auth.py
    employee.py
    manager.py
    admin.py
  services/
    goal_service.py
    escalation_service.py
    notification_service.py
  templates/
    admin/
    manager/
    employee/
    shared/
```

## Local Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

Create a `.env` file at project root:

```env
DATABASE_URL=sqlite:///./atomquest.db
SECRET_KEY=replace-with-a-long-random-secret
ACCESS_TOKEN_EXPIRE_MINUTES=60

# Optional (for /setup route during demo seeding)
ALLOW_SETUP_ROUTE=false

# Optional (for email queue dispatch)
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
```

### 3. Run app

```bash
uvicorn app.main:app --reload
```

Open: `http://127.0.0.1:8000/login`

## Docker Run

```bash
docker build -t atomquest-portal .
docker run --rm -p 7860:7860 \
  -e DATABASE_URL="sqlite:///./atomquest.db" \
  -e SECRET_KEY="replace-me" \
  atomquest-portal
```

## Demo/Test User Seeding

The app has a temporary setup route:

- `GET /setup` creates test users
- it only works when `ALLOW_SETUP_ROUTE=true`

Recommended flow:

1. Set `ALLOW_SETUP_ROUTE=true`
2. Call `/setup` once
3. Set `ALLOW_SETUP_ROUTE=false` again

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | Yes | DB connection string (SQLite/Postgres) |
| `SECRET_KEY` | Yes | JWT signing secret |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | Token expiry in minutes (default `60`) |
| `ALLOW_SETUP_ROUTE` | No | Enables `/setup` route when `true` |
| `SMTP_HOST` | No | SMTP host for queued email send |
| `SMTP_PORT` | No | SMTP port (default `587`) |
| `SMTP_USER` | No | SMTP username |
| `SMTP_PASSWORD` | No | SMTP password |
| `SMTP_FROM` | No | Sender email |

## PostgreSQL Example

```env
DATABASE_URL=postgresql+psycopg2://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require
```

If password has special characters (`@`, `#`, `/`, `%`, `:`), URL-encode it.

## Common Troubleshooting

- **Login fails for test users**
  - `/setup` was not run (or `ALLOW_SETUP_ROUTE` is false)
  - app points to a different DB than expected
- **`/setup` fails**
  - check deployment logs and DB connectivity
  - verify `DATABASE_URL` format and DB user permissions
- **Postgres connection error**
  - ensure `DATABASE_URL` is uppercase and correctly formatted
  - ensure `psycopg2-binary` is installed
- **Email sending fails**
  - SMTP env vars missing/invalid
  - queued items stay in `queued` or move to `failed`

## Hackathon Demo Checklist

1. `DATABASE_URL` and `SECRET_KEY` configured
2. Role logins working (employee/manager/admin)
3. One complete role flow rehearsed:
   - employee goal + submit
   - manager approve/check-in
   - admin completion/analytics/escalation view
4. `ALLOW_SETUP_ROUTE=false` before final demo