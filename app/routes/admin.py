from datetime import datetime
import csv
import io

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import Goal, User, AuditLog, Achievement, CheckIn, CheckInWindow
from app.services.goal_service import (
    QUARTERS,
    create_audit_log,
    create_shared_goal_bundle,
    get_scheduled_checkin_quarter,
    is_goal_setting_open,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def render_shared_goals_page(request: Request, user, db: Session, error: str = "", success: str = ""):
    employees = db.query(User).filter(User.role == "employee").all()
    return templates.TemplateResponse(request, "admin/shared_goals.html", {
        "user": user,
        "employees": employees,
        "error": error,
        "success": success,
        "goal_setting_open": is_goal_setting_open(),
    })


@router.get("/dashboard")
def dashboard(request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
    total_employees = db.query(User).filter(User.role == "employee").count()
    total_managers = db.query(User).filter(User.role == "manager").count()
    employees = db.query(User).filter(User.role == "employee").all()

    stats = {
        "total_employees": total_employees,
        "total_managers": total_managers,
        "not_started": 0,
        "submitted": 0,
        "approved": 0,
    }
    for emp in employees:
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
        "stats": stats,
        "goal_setting_open": is_goal_setting_open(),
        "current_quarter": get_scheduled_checkin_quarter(),
    })


@router.get("/audit")
def audit_trail(request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
    logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(300).all()
    return templates.TemplateResponse(request, "admin/audit.html", {
        "user": user,
        "logs": logs,
    })


@router.get("/employees")
def all_employees(request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
    employees = db.query(User).filter(User.role == "employee").all()
    employee_data = []
    for emp in employees:
        goals = db.query(Goal).filter(Goal.employee_id == emp.id).all()
        employee_data.append({"employee": emp, "goals": goals})
    return templates.TemplateResponse(request, "admin/employees.html", {
        "user": user,
        "employee_data": employee_data,
    })


@router.post("/unlock/{goal_id}")
def unlock_goal(goal_id: int, user=Depends(require_admin), db: Session = Depends(get_db)):
    goal = db.query(Goal).filter(Goal.id == goal_id).first()
    if goal and goal.status == "locked":
        create_audit_log(db, goal_id, user.id, "status", "locked", "draft")
        goal.status = "draft"
        db.commit()
    return RedirectResponse(url="/admin/employees", status_code=302)


@router.get("/shared-goals")
def shared_goals_page(request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
    return render_shared_goals_page(request, user, db)


@router.post("/shared-goals/create")
def create_shared_goals(
    request: Request,
    owner_id: int = Form(...),
    recipient_ids: list[int] = Form(...),
    title: str = Form(...),
    thrust_area: str = Form(...),
    uom_type: str = Form(...),
    target: float = Form(...),
    owner_weightage: float = Form(...),
    recipient_weightage: float = Form(...),
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not is_goal_setting_open():
        return render_shared_goals_page(request, user, db, error="Shared goals can be pushed only in the goal-setting window (May-June).")

    try:
        _, recipient_count = create_shared_goal_bundle(
            db=db,
            owner_id=owner_id,
            recipient_ids=recipient_ids,
            title=title,
            thrust_area=thrust_area,
            uom_type=uom_type,
            target=target,
            owner_weightage=owner_weightage,
            recipient_weightage=recipient_weightage,
        )
    except HTTPException as e:
        return render_shared_goals_page(request, user, db, error=str(e.detail))

    return render_shared_goals_page(
        request,
        user,
        db,
        success=f"Shared KPI created for primary owner and {recipient_count} recipients.",
    )


@router.get("/completion")
def completion_dashboard(
    request: Request,
    quarter: str = "",
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    selected_quarter = quarter if quarter in QUARTERS else (get_scheduled_checkin_quarter() or "Q1")

    employees = db.query(User).filter(User.role == "employee").all()
    employee_rows = []
    employee_complete_count = 0
    for emp in employees:
        locked_goals = db.query(Goal).filter(Goal.employee_id == emp.id, Goal.status == "locked").all()
        locked_goal_ids = [g.id for g in locked_goals]
        completed_count = 0
        if locked_goal_ids:
            completed_count = (
                db.query(Achievement.goal_id)
                .filter(Achievement.goal_id.in_(locked_goal_ids), Achievement.quarter == selected_quarter)
                .distinct()
                .count()
            )
        done = bool(locked_goal_ids) and completed_count == len(locked_goal_ids)
        if done:
            employee_complete_count += 1
        employee_rows.append({
            "employee": emp,
            "total": len(locked_goal_ids),
            "completed": completed_count,
            "done": done,
        })

    managers = db.query(User).filter(User.role == "manager").all()
    manager_rows = []
    manager_complete_count = 0
    for mgr in managers:
        team_ids = [u[0] for u in db.query(User.id).filter(User.manager_id == mgr.id, User.role == "employee").all()]
        team_goal_ids = [g[0] for g in db.query(Goal.id).filter(Goal.employee_id.in_(team_ids), Goal.status == "locked").all()] if team_ids else []
        completed_count = 0
        if team_goal_ids:
            completed_count = (
                db.query(CheckIn.goal_id)
                .filter(
                    CheckIn.goal_id.in_(team_goal_ids),
                    CheckIn.quarter == selected_quarter,
                    CheckIn.manager_id == mgr.id,
                )
                .distinct()
                .count()
            )
        done = bool(team_goal_ids) and completed_count == len(team_goal_ids)
        if done:
            manager_complete_count += 1
        manager_rows.append({
            "manager": mgr,
            "total": len(team_goal_ids),
            "completed": completed_count,
            "done": done,
        })

    return templates.TemplateResponse(request, "admin/completion.html", {
        "user": user,
        "quarters": QUARTERS,
        "selected_quarter": selected_quarter,
        "employee_rows": employee_rows,
        "manager_rows": manager_rows,
        "employee_complete_count": employee_complete_count,
        "manager_complete_count": manager_complete_count,
        "employee_total": len(employees),
        "manager_total": len(managers),
    })


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
                        a.quarter, a.actual, f"{round(a.score * 100, 1)}%",
                    ])
            else:
                writer.writerow([
                    emp.name, manager_name, goal.title, goal.thrust_area,
                    goal.uom_type, goal.target, goal.weightage, goal.status,
                    "N/A", "N/A", "N/A",
                ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=atomquest_report.csv"},
    )


@router.get("/checkin-windows")
def checkin_windows(request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
    windows = {w.quarter: w for w in db.query(CheckInWindow).all()}
    return templates.TemplateResponse(request, "admin/checkin_windows.html", {
        "user": user,
        "quarters": QUARTERS,
        "windows": windows,
        "current_quarter": get_scheduled_checkin_quarter(),
    })


@router.post("/checkin-windows/toggle/{quarter}")
def toggle_window(quarter: str, user=Depends(require_admin), db: Session = Depends(get_db)):
    if quarter not in QUARTERS:
        return RedirectResponse(url="/admin/checkin-windows", status_code=302)
    window = db.query(CheckInWindow).filter(CheckInWindow.quarter == quarter).first()
    if window:
        window.is_open = not window.is_open
        window.updated_at = datetime.utcnow()
        window.opened_by = user.id
    else:
        db.add(CheckInWindow(quarter=quarter, is_open=True, opened_by=user.id))
    db.commit()
    return RedirectResponse(url="/admin/checkin-windows", status_code=302)
