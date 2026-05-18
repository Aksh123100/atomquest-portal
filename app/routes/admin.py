from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Goal, User, AuditLog, Achievement, CheckIn, CheckInWindow
from app.auth import require_admin
import csv
import io
from datetime import datetime

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ── Dashboard ─────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
    total_employees = db.query(User).filter(User.role == "employee").count()
    total_managers = db.query(User).filter(User.role == "manager").count()

    submitted = db.query(User).filter(User.role == "employee").all()
    
    stats = {
        "total_employees": total_employees,
        "total_managers": total_managers,
        "not_started": 0,
        "submitted": 0,
        "approved": 0,
    }

    for emp in submitted:
        goals = db.query(Goal).filter(Goal.employee_id == emp.id).all()
        if not goals:
            stats["not_started"] += 1
        elif any(g.status == "locked" for g in goals):
            stats["approved"] += 1
        elif any(g.status == "submitted" for g in goals):
            stats["submitted"] += 1
        else:
            stats["not_started"] += 1

    return templates.TemplateResponse(request, "admin/dashboard.html", {
        "user": user,
        "stats": stats
    })


# ── Audit Trail ───────────────────────────────────────────

@router.get("/audit")
def audit_trail(request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
    logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(200).all()
    return templates.TemplateResponse(request, "admin/audit.html", {
        "user": user,
        "logs": logs
    })


# ── All Employees & Goals ─────────────────────────────────

@router.get("/employees")
def all_employees(request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
    employees = db.query(User).filter(User.role == "employee").all()
    employee_data = []
    for emp in employees:
        goals = db.query(Goal).filter(Goal.employee_id == emp.id).all()
        employee_data.append({"employee": emp, "goals": goals})
    return templates.TemplateResponse(request, "admin/employees.html", {
        "user": user,
        "employee_data": employee_data
    })


# ── Unlock Goal ───────────────────────────────────────────

@router.post("/unlock/{goal_id}")
def unlock_goal(goal_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    goal = db.query(Goal).filter(Goal.id == goal_id).first()
    if goal and goal.status == "locked":
        # Log the unlock in audit trail
        db.add(AuditLog(
            goal_id=goal_id,
            changed_by=user.id,
            field_changed="status",
            old_value="locked",
            new_value="draft"
        ))
        goal.status = "draft"
        db.commit()
    return RedirectResponse(url="/admin/employees", status_code=302)


# ── CSV Export ────────────────────────────────────────────

@router.get("/export")
def export_csv(user=Depends(require_admin), db: Session = Depends(get_db)):
    employees = db.query(User).filter(User.role == "employee").all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Employee", "Manager", "Goal", "Thrust Area", "UoM", "Target", "Weightage", "Status", "Quarter", "Actual", "Score"])

    for emp in employees:
        manager = db.query(User).filter(User.id == emp.manager_id).first()
        manager_name = manager.name if manager else "N/A"
        goals = db.query(Goal).filter(Goal.employee_id == emp.id).all()
        for goal in goals:
            achievements = db.query(Achievement).filter(Achievement.goal_id == goal.id).all()
            if achievements:
                for a in achievements:
                    writer.writerow([
                        emp.name, manager_name, goal.title, goal.thrust_area,
                        goal.uom_type, goal.target, goal.weightage, goal.status,
                        a.quarter, a.actual, f"{round(a.score * 100, 1)}%"
                    ])
            else:
                writer.writerow([
                    emp.name, manager_name, goal.title, goal.thrust_area,
                    goal.uom_type, goal.target, goal.weightage, goal.status,
                    "N/A", "N/A", "N/A"
                ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=atomquest_report.csv"}
    )


# ── Check-in Windows ──────────────────────────────────────

@router.get("/checkin-windows")
def checkin_windows(request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
    quarters = ["Q1", "Q2", "Q3", "Q4"]
    windows = {w.quarter: w for w in db.query(CheckInWindow).all()}
    return templates.TemplateResponse(request, "admin/checkin_windows.html", {
        "user": user,
        "quarters": quarters,
        "windows": windows
    })

@router.post("/checkin-windows/toggle/{quarter}")
def toggle_window(quarter: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    window = db.query(CheckInWindow).filter(CheckInWindow.quarter == quarter).first()
    if window:
        window.is_open = not window.is_open
        window.updated_at = datetime.utcnow()
        window.opened_by = user.id
    else:
        db.add(CheckInWindow(quarter=quarter, is_open=True, opened_by=user.id))
    db.commit()
    return RedirectResponse(url="/admin/checkin-windows", status_code=302)