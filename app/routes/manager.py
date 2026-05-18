from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Goal, User, CheckIn
from app.auth import require_manager
from app.services.goal_service import (
    create_audit_log,
    create_shared_goal_bundle,
    get_open_checkin_quarters,
    goal_was_previously_locked,
    is_goal_setting_open,
    is_shared_recipient_goal,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def get_team(db: Session, manager_id: int):
    return db.query(User).filter(User.manager_id == manager_id, User.role == "employee").all()


def render_shared_goals_page(request: Request, user, db: Session, error: str = "", success: str = ""):
    team = get_team(db, user.id)
    return templates.TemplateResponse(request, "manager/shared_goals.html", {
        "user": user,
        "team": team,
        "error": error,
        "success": success,
        "goal_setting_open": is_goal_setting_open(),
    })


@router.get("/dashboard")
def dashboard(request: Request, user=Depends(require_manager), db: Session = Depends(get_db)):
    team = get_team(db, user.id)
    pending_employees = sum(
        1 for member in team
        if db.query(Goal).filter(Goal.employee_id == member.id, Goal.status == "submitted").first()
    )
    return templates.TemplateResponse(request, "manager/dashboard.html", {
        "user": user,
        "team": team,
        "pending_count": pending_employees,
    })


@router.get("/team")
def team_goals(request: Request, user=Depends(require_manager), db: Session = Depends(get_db)):
    team = get_team(db, user.id)
    team_goals = {member: db.query(Goal).filter(Goal.employee_id == member.id).all() for member in team}
    return templates.TemplateResponse(request, "manager/team.html", {
        "user": user,
        "team_goals": team_goals,
    })


@router.get("/shared-goals")
def shared_goals_page(request: Request, user=Depends(require_manager), db: Session = Depends(get_db)):
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
    user=Depends(require_manager),
    db: Session = Depends(get_db),
):
    if not is_goal_setting_open():
        return render_shared_goals_page(request, user, db, error="Shared goals can be pushed only in the goal-setting window (May-June).")

    team_ids = {member.id for member in get_team(db, user.id)}
    if owner_id not in team_ids:
        return render_shared_goals_page(request, user, db, error="Primary owner must be from your reporting team.")

    if any(rid not in team_ids for rid in recipient_ids):
        return render_shared_goals_page(request, user, db, error="All recipients must be from your reporting team.")

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


@router.get("/approve")
def approve_page(request: Request, user=Depends(require_manager), db: Session = Depends(get_db)):
    team = get_team(db, user.id)
    employee_sheets = []
    for member in team:
        goals = db.query(Goal).filter(Goal.employee_id == member.id, Goal.status == "submitted").all()
        if goals:
            total = sum(g.weightage for g in goals)
            employee_sheets.append({
                "employee": member,
                "goals": goals,
                "total": round(total, 1),
                "is_valid": round(total) == 100,
            })
    return templates.TemplateResponse(request, "manager/approve.html", {
        "user": user,
        "employee_sheets": employee_sheets,
        "goal_setting_open": is_goal_setting_open(),
    })


@router.post("/approve-sheet-final/{employee_id}")
async def approve_sheet_final(
    employee_id: int,
    user=Depends(require_manager),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if not is_goal_setting_open():
        return {"success": False, "error": "Approvals are allowed only in the goal-setting window (May-June)."}

    employee = db.query(User).filter(User.id == employee_id, User.manager_id == user.id, User.role == "employee").first()
    if not employee:
        return {"success": False, "error": "Employee not found in your team."}

    data = await request.json()
    goals_data = data.get("goals", [])
    comment = data.get("comment", "").strip()

    goals = db.query(Goal).filter(Goal.employee_id == employee_id, Goal.status == "submitted").all()
    goal_map = {g.id: g for g in goals}
    if not goals:
        return {"success": False, "error": "No submitted goals found for this employee."}
    if len(goals_data) != len(goals):
        return {"success": False, "error": "Approval payload does not include all submitted goals."}

    total = 0.0
    for g_data in goals_data:
        goal = goal_map.get(int(g_data["id"]))
        if not goal:
            return {"success": False, "error": "Invalid goal payload."}

        weightage = float(g_data["weightage"])
        if weightage < 10:
            return {"success": False, "error": f"Goal '{goal.title}' must have at least 10% weightage."}
        total += weightage

        if is_shared_recipient_goal(goal) and float(g_data["target"]) != goal.target:
            return {"success": False, "error": f"Target for shared goal '{goal.title}' is read-only for recipients."}

    if round(total) != 100:
        return {"success": False, "error": f"Total weightage is {total}%, must be 100%"}

    for g_data in goals_data:
        goal = goal_map[int(g_data["id"])]
        new_target = float(g_data["target"])
        new_weightage = float(g_data["weightage"])

        if not is_shared_recipient_goal(goal) and goal.target != new_target:
            if goal.status == "locked" or goal_was_previously_locked(db, goal.id):
                create_audit_log(db, goal.id, user.id, "target", goal.target, new_target)
            goal.target = new_target

        if goal.weightage != new_weightage:
            if goal.status == "locked" or goal_was_previously_locked(db, goal.id):
                create_audit_log(db, goal.id, user.id, "weightage", goal.weightage, new_weightage)
            goal.weightage = new_weightage

    for goal in goals:
        if goal.status != "locked":
            goal.status = "locked"

        if comment:
            existing = db.query(CheckIn).filter(
                CheckIn.goal_id == goal.id,
                CheckIn.quarter == "approval",
                CheckIn.manager_id == user.id,
            ).first()
            if existing:
                existing.comment = comment
            else:
                db.add(CheckIn(goal_id=goal.id, manager_id=user.id, quarter="approval", comment=comment))

    db.commit()
    return {"success": True}


@router.post("/reject-sheet/{employee_id}")
async def reject_sheet(
    employee_id: int,
    user=Depends(require_manager),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if not is_goal_setting_open():
        return {"success": False, "error": "Rework decisions are allowed only in the goal-setting window (May-June)."}

    employee = db.query(User).filter(User.id == employee_id, User.manager_id == user.id, User.role == "employee").first()
    if not employee:
        return {"success": False, "error": "Employee not found in your team."}

    data = await request.json()
    comment = data.get("comment", "").strip()
    goals_data = data.get("goals", [])
    if not comment:
        return {"success": False, "error": "Comment is required when returning for rework"}

    goals = db.query(Goal).filter(Goal.employee_id == employee_id, Goal.status == "submitted").all()
    goal_map = {g.id: g for g in goals}
    if not goals:
        return {"success": False, "error": "No submitted goals found for this employee."}
    if len(goals_data) != len(goals):
        return {"success": False, "error": "Rework payload does not include all submitted goals."}

    total = 0.0
    for g_data in goals_data:
        goal = goal_map.get(int(g_data["id"]))
        if not goal:
            return {"success": False, "error": "Invalid goal payload."}
        weightage = float(g_data["weightage"])
        if weightage < 10:
            return {"success": False, "error": f"Goal '{goal.title}' must have at least 10% weightage."}
        total += weightage
        if is_shared_recipient_goal(goal) and float(g_data["target"]) != goal.target:
            return {"success": False, "error": f"Target for shared goal '{goal.title}' is read-only for recipients."}

    if round(total) != 100:
        return {"success": False, "error": f"Total weightage is {total}%, must be 100% before returning for rework."}

    for g_data in goals_data:
        goal = goal_map[int(g_data["id"])]
        new_target = float(g_data["target"])
        new_weightage = float(g_data["weightage"])
        changed_fields = []

        if not is_shared_recipient_goal(goal) and goal.target != new_target:
            changed_fields.append("target")
            goal.target = new_target
        if goal.weightage != new_weightage:
            changed_fields.append("weightage")
            goal.weightage = new_weightage

        if changed_fields:
            update_text = "Manager updated " + " & ".join(changed_fields)
            existing_update = db.query(CheckIn).filter(
                CheckIn.goal_id == goal.id,
                CheckIn.quarter == "rework_update",
                CheckIn.manager_id == user.id,
            ).first()
            if existing_update:
                existing_update.comment = update_text
            else:
                db.add(CheckIn(goal_id=goal.id, manager_id=user.id, quarter="rework_update", comment=update_text))

    for goal in goals:
        goal.status = "draft"
        existing = db.query(CheckIn).filter(
            CheckIn.goal_id == goal.id,
            CheckIn.quarter == "rework",
            CheckIn.manager_id == user.id,
        ).first()
        if existing:
            existing.comment = comment
        else:
            db.add(CheckIn(goal_id=goal.id, manager_id=user.id, quarter="rework", comment=comment))

    db.commit()
    return {"success": True}


@router.get("/checkin")
def checkin_page(request: Request, user=Depends(require_manager), db: Session = Depends(get_db)):
    team = get_team(db, user.id)
    team_ids = [u.id for u in team]
    goals = db.query(Goal).filter(Goal.employee_id.in_(team_ids), Goal.status == "locked").all()
    open_quarters = get_open_checkin_quarters(db)
    return templates.TemplateResponse(request, "manager/checkin.html", {
        "user": user,
        "goals": goals,
        "quarters": ["Q1", "Q2", "Q3", "Q4"],
        "open_quarters": open_quarters,
        "error": request.query_params.get("error"),
        "success": request.query_params.get("success"),
    })


@router.post("/checkin/save")
def save_checkin(
    user=Depends(require_manager),
    db: Session = Depends(get_db),
    goal_id: int = Form(...),
    quarter: str = Form(...),
    comment: str = Form(...),
):
    goal = (
        db.query(Goal)
        .join(User, User.id == Goal.employee_id)
        .filter(Goal.id == goal_id, User.manager_id == user.id, Goal.status == "locked")
        .first()
    )
    if not goal:
        return RedirectResponse(url="/manager/checkin?error=Goal not found in your team.", status_code=302)

    if quarter not in get_open_checkin_quarters(db):
        return RedirectResponse(url="/manager/checkin?error=Check-in is allowed only in the active quarter window.", status_code=302)

    existing = db.query(CheckIn).filter(
        CheckIn.goal_id == goal_id,
        CheckIn.quarter == quarter,
        CheckIn.manager_id == user.id,
    ).first()
    if existing:
        existing.comment = comment
    else:
        db.add(CheckIn(goal_id=goal_id, manager_id=user.id, quarter=quarter, comment=comment))
    db.commit()
    return RedirectResponse(url="/manager/checkin?success=Comment saved.", status_code=302)
