from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Goal, User, CheckIn
from app.auth import require_manager

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/dashboard")
def dashboard(request: Request, user=Depends(require_manager), db: Session = Depends(get_db)):
    team = db.query(User).filter(User.manager_id == user.id).all()
    pending_employees = sum(
        1 for member in team
        if db.query(Goal).filter(Goal.employee_id == member.id, Goal.status == "submitted").first()
    )
    return templates.TemplateResponse(request, "manager/dashboard.html", {
        "user": user, "team": team, "pending_count": pending_employees
    })


@router.get("/team")
def team_goals(request: Request, user=Depends(require_manager), db: Session = Depends(get_db)):
    team = db.query(User).filter(User.manager_id == user.id).all()
    team_goals = {member: db.query(Goal).filter(Goal.employee_id == member.id).all() for member in team}
    return templates.TemplateResponse(request, "manager/team.html", {
        "user": user, "team_goals": team_goals
    })


@router.get("/approve")
def approve_page(request: Request, user=Depends(require_manager), db: Session = Depends(get_db)):
    team = db.query(User).filter(User.manager_id == user.id).all()
    employee_sheets = []
    for member in team:
        goals = db.query(Goal).filter(Goal.employee_id == member.id, Goal.status == "submitted").all()
        if goals:
            total = sum(g.weightage for g in goals)
            employee_sheets.append({
                "employee": member,
                "goals": goals,
                "total": round(total, 1),
                "is_valid": round(total) == 100
            })
    return templates.TemplateResponse(request, "manager/approve.html", {
        "user": user, "employee_sheets": employee_sheets
    })


@router.post("/approve-sheet/{employee_id}")
def approve_sheet(
    employee_id: int,
    user=Depends(require_manager),
    db: Session = Depends(get_db),
    comment: str = Form(default=""),
    # We receive updated goals as form fields: target_{id} and weightage_{id}
    **kwargs
):
    # This endpoint now receives all edits + comment in one shot
    # But FastAPI doesn't easily support dynamic form keys — handled via JS fetch below
    pass


# New: single endpoint that receives JSON with all edits + approves
from fastapi import Body
import json

@router.post("/approve-sheet-final/{employee_id}")
async def approve_sheet_final(
    employee_id: int,
    user=Depends(require_manager),
    db: Session = Depends(get_db),
    request: Request = None
):
    data = await request.json()
    goals_data = data.get("goals", [])  # [{id, target, weightage}, ...]
    comment = data.get("comment", "")

    # Validate total
    total = sum(float(g["weightage"]) for g in goals_data)
    if round(total) != 100:
        return {"success": False, "error": f"Total weightage is {total}%, must be 100%"}

    # Save all edits
    goals = db.query(Goal).filter(Goal.employee_id == employee_id, Goal.status == "submitted").all()
    goal_map = {g.id: g for g in goals}

    for g_data in goals_data:
        goal = goal_map.get(int(g_data["id"]))
        if goal:
            goal.target = float(g_data["target"])
            goal.weightage = float(g_data["weightage"])

    # Lock all goals
    for goal in goals:
        goal.status = "locked"
        if comment:
            # Save manager comment as a CheckIn note (Q0 = approval stage)
            existing = db.query(CheckIn).filter(
                CheckIn.goal_id == goal.id, CheckIn.quarter == "rework"
            ).first()
            if existing:
                existing.comment = comment
            else:
                db.add(CheckIn(goal_id=goal.id, manager_id=user.id, quarter="rework", comment=comment))

    db.commit()
    return {"success": True}


@router.post("/reject-sheet/{employee_id}")
async def reject_sheet(
    employee_id: int,
    user=Depends(require_manager),
    db: Session = Depends(get_db),
    request: Request = None
):
    data = await request.json()
    comment = data.get("comment", "").strip()

    if not comment:
        return {"success": False, "error": "Comment is required when returning for rework"}

    goals = db.query(Goal).filter(Goal.employee_id == employee_id, Goal.status == "submitted").all()
    for goal in goals:
        goal.status = "draft"
        existing = db.query(CheckIn).filter(
            CheckIn.goal_id == goal.id, CheckIn.quarter == "rework"
        ).first()
        if existing:
            existing.comment = comment
        else:
            db.add(CheckIn(goal_id=goal.id, manager_id=user.id, quarter="rework", comment=comment))

    db.commit()
    return {"success": True}


@router.get("/checkin")
def checkin_page(request: Request, user=Depends(require_manager), db: Session = Depends(get_db)):
    team = db.query(User).filter(User.manager_id == user.id).all()
    team_ids = [u.id for u in team]
    goals = db.query(Goal).filter(Goal.employee_id.in_(team_ids), Goal.status == "locked").all()
    return templates.TemplateResponse(request, "manager/checkin.html", {
        "user": user, "goals": goals, "quarters": ["Q1", "Q2", "Q3", "Q4"]
    })


@router.post("/checkin/save")
def save_checkin(
    user=Depends(require_manager),
    db: Session = Depends(get_db),
    goal_id: int = Form(...),
    quarter: str = Form(...),
    comment: str = Form(...)
):
    existing = db.query(CheckIn).filter(CheckIn.goal_id == goal_id, CheckIn.quarter == quarter).first()
    if existing:
        existing.comment = comment
    else:
        db.add(CheckIn(goal_id=goal_id, manager_id=user.id, quarter=quarter, comment=comment))
    db.commit()
    return RedirectResponse(url="/manager/checkin", status_code=302)