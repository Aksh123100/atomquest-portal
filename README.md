---
title: AtomQuest Portal
emoji: ⚡
colorFrom: blue
colorTo: purple
sdk: docker
pinned: false
---

# ⚡ AtomQuest Portal

> **🌐 Live Demo → [https://aksh190-atomquest-portal.hf.space](https://aksh190-atomquest-portal.hf.space)**

A role-based KPI and quarterly performance tracking system built with FastAPI — supporting the full goal lifecycle from drafting to completion analytics, across three distinct roles: **Employee**, **Manager**, and **Admin**.

---

## 🧭 What It Does

AtomQuest Portal digitizes the entire performance management cycle inside an organization:

- Employees draft and submit KPI goals with weightage and thrust area classification
- Managers review, approve, request rework, and submit quarterly check-in comments
- Admins oversee completion dashboards, escalations, audit trails, and email dispatch

Everything is tracked — every status change, every comment, every missed deadline.

---

## ✨ Features

## 🔑 Demo Credentials

All accounts use the same password: `password123`

| Email | Role | Access |
|---|---|---|
| admin@test.com | Admin / HR | Completion dashboard, audit trail, escalations, export |
| manager@test.com | Manager | Approve goals, team check-ins, shared goals |
| employee@test.com | Employee | Create goals, quarterly check-in |
| employee2@test.com | Employee | Create goals, quarterly check-in |

> These accounts are pre-seeded automatically on startup. No need to call `/setup` manually.

### 👤 Employee
- Draft goals with `title`, `thrust area`, `UOM type` (min / max / zero / timeline), `target`, and `weightage`
- Submit goals for manager review
- Log quarterly achievements (Q1–Q4) during open check-in windows
- View goal status and score progression

### 🧑‍💼 Manager
- Approve or send goals back for rework with comments
- Add quarterly check-in comments per goal per employee
- View and manage shared (cascaded) goals
- Track team performance and escalation flags

### 🛡️ Admin
- **Completion Dashboard** — org-wide goal completion status
- **Analytics Dashboard** — performance metrics across teams
- **Escalation Center** — flag missed check-ins and overdue reviews
- **Email Queue** — dispatch queued SMTP notifications
- **Audit Trail** — full field-level change history per goal
- **Check-in Window Control** — toggle active quarter windows
- **CSV Export** — export goal data for reporting

---

## 🛠️ Tech Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI |
| ORM / DB | SQLAlchemy + SQLite (dev) / PostgreSQL (prod) |
| Frontend | Jinja2 templates + Tailwind CSS (CDN) |
| Auth | JWT cookies + passlib bcrypt |
| Deploy | Docker (Hugging Face Spaces compatible) |

---

## 📁 Project Structure

```
atomquest-portal/
├── app/
│   ├── main.py
│   ├── auth.py
│   ├── database.py
│   ├── models.py
│   ├── routes/
│   │   ├── auth.py
│   │   ├── employee.py
│   │   ├── manager.py
│   │   └── admin.py
│   ├── services/
│   │   ├── goal_service.py
│   │   ├── escalation_service.py
│   │   ├── notification_service.py
│   │   └── demo_seed_service.py
│   └── templates/
│       ├── admin/
│       ├── manager/
│       ├── employee/
│       └── shared/
├── static/
├── Dockerfile
├── requirements.txt
└── .env
```

---

## 🚀 Local Setup

### 1. Clone & install

```bash
git clone https://github.com/Aksh123100/atomquest-portal.git
cd atomquest-portal
pip install -r requirements.txt
```

### 2. Configure environment

Create a `.env` file at the project root:

```env
DATABASE_URL=sqlite:///./atomquest.db
SECRET_KEY=replace-with-a-long-random-secret
ACCESS_TOKEN_EXPIRE_MINUTES=60

# Enable demo user seeding (disable after first run)
ALLOW_SETUP_ROUTE=true

# Optional — SMTP email dispatch
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
```

### 3. Run

```bash
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/login`

### 4. Seed demo users

With `ALLOW_SETUP_ROUTE=true`, hit:

```
GET /setup
```

Then set `ALLOW_SETUP_ROUTE=false` to lock it down.

---

## 🐳 Docker

```bash
docker build -t atomquest-portal .

docker run --rm -p 7860:7860 \
  -e DATABASE_URL="sqlite:///./atomquest.db" \
  -e SECRET_KEY="replace-me" \
  atomquest-portal
```

---

## 🌐 Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | ✅ | — | SQLite or PostgreSQL connection string |
| `SECRET_KEY` | ✅ | — | JWT signing secret (make it long and random) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | ❌ | `60` | Token lifetime in minutes |
| `ALLOW_SETUP_ROUTE` | ❌ | `false` | Enables `/setup` for demo seeding |
| `SMTP_HOST` | ❌ | — | SMTP server hostname |
| `SMTP_PORT` | ❌ | `587` | SMTP port |
| `SMTP_USER` | ❌ | — | SMTP username |
| `SMTP_PASSWORD` | ❌ | — | SMTP password |
| `SMTP_FROM` | ❌ | — | Sender email address |

### PostgreSQL Example

```env
DATABASE_URL=postgresql+psycopg2://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require
```

> If your password contains special characters (`@`, `#`, `/`, `%`, `:`), URL-encode them.

---

## 🔧 Troubleshooting

| Issue | Fix |
|---|---|
| Login fails for test users | Run `/setup` with `ALLOW_SETUP_ROUTE=true` first |
| `/setup` route not found | Check that `ALLOW_SETUP_ROUTE=true` is set |
| PostgreSQL connection error | Verify `DATABASE_URL` format and that `psycopg2-binary` is installed |
| Email sending fails | Check SMTP env vars; failed sends stay in `queued` or move to `failed` state |
| App starts but DB is empty | Check `DATABASE_URL` points to the right file/host |

---

## 📊 Data Model (Key Entities)

```
User ──< Goal ──< Achievement (Q1–Q4 scores)
              └─< AuditLog   (field-level change history)
              └─ CheckIn     (manager quarter comments)

CheckInWindow  (admin-controlled per-quarter toggle)
Notification   (email queue with status tracking)
```

Goal statuses: `draft → submitted → approved → locked`  
Achievement statuses: `not_started → on_track → completed`

---

## 📋 Demo Checklist

Before a live demo, verify:

- [ ] `DATABASE_URL` and `SECRET_KEY` are set
- [ ] `/setup` was called and all three role logins work
- [ ] `ALLOW_SETUP_ROUTE=false` is set for the final run
- [ ] One full role flow rehearsed:
  - **Employee** — draft a goal → submit
  - **Manager** — approve → add check-in comment
  - **Admin** — view completion dashboard → trigger escalation check

---

## 🧑‍💻 Author

Built by [Aksh Singhal](https://github.com/Aksh123100)
