from datetime import datetime
import csv
import io
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import Goal, User, AuditLog, Achievement, CheckIn, CheckInWindow, Escalation, Notification
from app.services.goal_service import (
    QUARTERS,
    create_audit_log,
    create_shared_goal_bundle,
    get_open_checkin_quarters,
    get_scheduled_checkin_quarter,
    is_goal_setting_open,
)
from app.services.escalation_service import run_quarter_escalation_scan
from app.services.demo_seed_service import seed_demo_data
from app.services.notification_service import send_queued_emails

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


def get_reportable_quarters(db: Session) -> set[str]:
    return set(get_open_checkin_quarters(db))


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
        "open_escalations": db.query(Escalation).filter(Escalation.status == "open").count(),
        "queued_emails": db.query(Notification).filter(Notification.status == "queued").count(),
        "error": request.query_params.get("error"),
        "success": request.query_params.get("success"),
    })


@router.post("/demo-seed")
def seed_demo_dataset(user=Depends(require_admin), db: Session = Depends(get_db)):
    try:
        result = seed_demo_data(db)
    except ValueError as exc:
        return RedirectResponse(url=f"/admin/dashboard?error={quote_plus(str(exc))}", status_code=302)

    message = (
        "Demo dataset ready. "
        f"Employee #{result['employee_id']}, "
        f"Secondary employee #{result['secondary_employee_id']}, "
        f"Manager #{result['manager_id']}."
    )
    return RedirectResponse(url=f"/admin/dashboard?success={quote_plus(message)}", status_code=302)


@router.get("/audit")
def audit_trail(request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
    logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(300).all()
    users = {u.id: u for u in db.query(User).all()}
    return templates.TemplateResponse(request, "admin/audit.html", {
        "user": user,
        "logs": logs,
        "users": users,
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
    reportable_quarters = get_reportable_quarters(db)
    selected_quarter = quarter if quarter in QUARTERS else (next(iter(sorted(reportable_quarters)), None) or "Q1")
    quarter_available = selected_quarter in reportable_quarters

    employees = db.query(User).filter(User.role == "employee").all()
    employee_rows = []
    employee_complete_count = 0
    for emp in employees:
        if not quarter_available:
            employee_rows.append({
                "employee": emp,
                "total": 0,
                "completed": 0,
                "done": False,
            })
            continue

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
        if not quarter_available:
            manager_rows.append({
                "manager": mgr,
                "total": 0,
                "completed": 0,
                "done": False,
            })
            continue

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
        "quarter_available": quarter_available,
        "employee_rows": employee_rows,
        "manager_rows": manager_rows,
        "employee_complete_count": employee_complete_count,
        "manager_complete_count": manager_complete_count,
        "employee_total": len(employees),
        "manager_total": len(managers),
    })


@router.get("/analytics")
def analytics_dashboard(request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
    employees = db.query(User).filter(User.role == "employee").all()
    managers = db.query(User).filter(User.role == "manager").all()
    total_employees = len(employees)

    employees_with_locked = 0
    for emp in employees:
        has_locked = db.query(Goal).filter(Goal.employee_id == emp.id, Goal.status == "locked").first() is not None
        if has_locked:
            employees_with_locked += 1

    funnel = {
        "not_started": 0,
        "draft": 0,
        "submitted": 0,
        "locked": 0,
    }
    for emp in employees:
        emp_goals = db.query(Goal).filter(Goal.employee_id == emp.id).all()
        if not emp_goals:
            funnel["not_started"] += 1
            continue
        statuses = {g.status for g in emp_goals}
        if "locked" in statuses:
            funnel["locked"] += 1
        elif "submitted" in statuses:
            funnel["submitted"] += 1
        else:
            funnel["draft"] += 1

    quarter_rows = []
    reportable_quarters = get_reportable_quarters(db)
    locked_goal_ids = [g[0] for g in db.query(Goal.id).filter(Goal.status == "locked").all()]
    total_locked = len(locked_goal_ids)
    for quarter in QUARTERS:
        if quarter not in reportable_quarters:
            quarter_rows.append({
                "quarter": quarter,
                "locked_goals": 0,
                "employee_completed": 0,
                "manager_reviewed": 0,
                "employee_rate": 0.0,
                "manager_rate": 0.0,
                "is_reportable": False,
            })
            continue

        employee_done = 0
        manager_done = 0
        if locked_goal_ids:
            employee_done = (
                db.query(Achievement.goal_id)
                .filter(Achievement.goal_id.in_(locked_goal_ids), Achievement.quarter == quarter)
                .distinct()
                .count()
            )
            manager_done = (
                db.query(CheckIn.goal_id)
                .filter(CheckIn.goal_id.in_(locked_goal_ids), CheckIn.quarter == quarter)
                .distinct()
                .count()
            )
        quarter_rows.append({
            "quarter": quarter,
            "locked_goals": total_locked,
            "employee_completed": employee_done,
            "manager_reviewed": manager_done,
            "employee_rate": round((employee_done / total_locked) * 100, 1) if total_locked else 0.0,
            "manager_rate": round((manager_done / total_locked) * 100, 1) if total_locked else 0.0,
            "is_reportable": True,
        })

    thrust_area_counts = {}
    for goal in db.query(Goal).all():
        thrust_area_counts[goal.thrust_area] = thrust_area_counts.get(goal.thrust_area, 0) + 1
    top_thrust_areas = sorted(
        [{"name": name, "count": count} for name, count in thrust_area_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:5]

    manager_risk_rows = []
    for mgr in managers:
        open_count = (
            db.query(Escalation)
            .filter(Escalation.manager_id == mgr.id, Escalation.status == "open")
            .count()
        )
        manager_risk_rows.append({
            "manager": mgr,
            "open_escalations": open_count,
        })
    manager_risk_rows.sort(key=lambda x: x["open_escalations"], reverse=True)

    return templates.TemplateResponse(request, "admin/analytics.html", {
        "user": user,
        "goal_coverage_pct": round((employees_with_locked / total_employees) * 100, 1) if total_employees else 0.0,
        "funnel": funnel,
        "quarter_rows": quarter_rows,
        "reportable_quarters": sorted(reportable_quarters, key=lambda x: QUARTERS.index(x)) if reportable_quarters else [],
        "top_thrust_areas": top_thrust_areas,
        "manager_risk_rows": manager_risk_rows,
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


@router.get("/escalations")
def escalations_page(
    request: Request,
    quarter: str = "",
    status: str = "open",
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    open_quarters = [q for q in QUARTERS if q in get_open_checkin_quarters(db)]
    if quarter and quarter in open_quarters:
        selected_quarter = quarter
    elif open_quarters:
        selected_quarter = open_quarters[0]
    else:
        selected_quarter = ""
    selected_status = status if status in {"open", "resolved", "all"} else "open"

    query = db.query(Escalation)
    if selected_quarter:
        query = query.filter(Escalation.quarter == selected_quarter)
    elif open_quarters:
        query = query.filter(Escalation.quarter.in_(open_quarters))
    if selected_status != "all":
        query = query.filter(Escalation.status == selected_status)

    escalations = query.order_by(Escalation.created_at.desc()).limit(300).all()
    employees = {u.id: u for u in db.query(User).filter(User.role == "employee").all()}
    managers = {u.id: u for u in db.query(User).filter(User.role == "manager").all()}
    goals = {g.id: g for g in db.query(Goal).all()}
    return templates.TemplateResponse(request, "admin/escalations.html", {
        "user": user,
        "escalations": escalations,
        "quarters": open_quarters,
        "selected_quarter": selected_quarter,
        "selected_status": selected_status,
        "open_quarters": open_quarters,
        "employees": employees,
        "managers": managers,
        "goals": goals,
        "error": request.query_params.get("error"),
        "success": request.query_params.get("success"),
    })


@router.post("/escalations/run")
def run_escalation_scan(
    quarter: str = Form(...),
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    open_quarters = set(get_open_checkin_quarters(db))
    if quarter not in QUARTERS:
        return RedirectResponse(url="/admin/escalations?error=Invalid quarter.", status_code=302)
    if quarter not in open_quarters:
        return RedirectResponse(
            url=f"/admin/escalations?error={quote_plus(f'Quarter {quarter} is not open for scanning.')}",
            status_code=302,
        )
    result = run_quarter_escalation_scan(db, quarter)
    message = (
        "Scan complete. "
        f"Employee escalations: {result['employee_missed_checkin_created']}, "
        f"Manager escalations: {result['manager_missed_review_created']}, "
        f"Auto-resolved employee: {result['employee_missed_checkin_auto_resolved']}, "
        f"Auto-resolved manager: {result['manager_missed_review_auto_resolved']}."
    )
    return RedirectResponse(
        url=f"/admin/escalations?quarter={quarter}&success={quote_plus(message)}",
        status_code=302,
    )


@router.post("/escalations/resolve/{escalation_id}")
def resolve_escalation(
    escalation_id: int,
    quarter: str = Form(default=""),
    status: str = Form(default="open"),
    user=Depends(require_admin),
    db: Session = Depends(get_db),
):
    escalation = db.query(Escalation).filter(Escalation.id == escalation_id).first()
    if not escalation:
        return RedirectResponse(url="/admin/escalations?error=Escalation not found.", status_code=302)
    escalation.status = "resolved"
    escalation.resolved_by = user.id
    escalation.resolved_at = datetime.utcnow()
    if escalation.goal_id:
        create_audit_log(db, escalation.goal_id, user.id, "escalation_status", "open", "resolved")
    db.commit()
    redirect_query = f"status={status if status in {'open', 'resolved', 'all'} else 'open'}"
    if quarter in QUARTERS:
        redirect_query = f"quarter={quarter}&{redirect_query}"
    return RedirectResponse(
        url=f"/admin/escalations?{redirect_query}&success={quote_plus('Escalation marked as resolved.')}",
        status_code=302,
    )


@router.get("/notifications")
def notifications_page(request: Request, user=Depends(require_admin), db: Session = Depends(get_db)):
    notifications = (
        db.query(Notification)
        .order_by(Notification.created_at.desc())
        .limit(300)
        .all()
    )
    users = {u.id: u for u in db.query(User).all()}
    return templates.TemplateResponse(request, "admin/notifications.html", {
        "user": user,
        "notifications": notifications,
        "users": users,
        "error": request.query_params.get("error"),
        "success": request.query_params.get("success"),
    })


@router.post("/notifications/send")
def send_notifications(user=Depends(require_admin), db: Session = Depends(get_db)):
    try:
        result = send_queued_emails(db, limit=200)
    except HTTPException as exc:
        return RedirectResponse(url=f"/admin/notifications?error={quote_plus(str(exc.detail))}", status_code=302)

    message = (
        "Email dispatch complete. "
        f"Attempted: {result['attempted']}, Sent: {result['sent']}, Failed: {result['failed']}."
    )
    return RedirectResponse(url=f"/admin/notifications?success={quote_plus(message)}", status_code=302)


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
