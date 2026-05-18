

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Goal, Achievement, CheckIn, CheckInWindow
from app.auth import require_employee
from app.services.goal_service import validate_goals, get_employee_goals, calculate_score

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ── Dashboard ─────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(request: Request, user=Depends(require_employee)):
    return templates.TemplateResponse(request, "employee/dashboard.html", {"user": user})


# ── View Goals ────────────────────────────────────────────

def get_rework_comments(db, goals):
    rework_comments = {}
    for goal in goals:
        comment = db.query(CheckIn).filter(
            CheckIn.goal_id == goal.id,
            CheckIn.quarter == "rework"
        ).order_by(CheckIn.created_at.desc()).first()
        if comment:
            rework_comments[goal.id] = comment.comment
    return rework_comments


@router.get("/goals")
def view_goals(request: Request, user=Depends(require_employee), db: Session = Depends(get_db)):
    goals = get_employee_goals(db, user.id)
    total_weightage = sum(g.weightage for g in goals)
    rework_comments = get_rework_comments(db, goals)
    return templates.TemplateResponse(request, "employee/goals.html", {
        "user": user,
        "goals": goals,
        "total_weightage": total_weightage,
        "rework_comments": rework_comments
    })


# ── Add Goal ──────────────────────────────────────────────

@router.post("/goals/add")
def add_goal(
    request: Request,
    user=Depends(require_employee),
    db: Session = Depends(get_db),
    title: str = Form(...),
    thrust_area: str = Form(...),
    uom_type: str = Form(...),
    target: float = Form(...),
    weightage: float = Form(...)
):
    goals = get_employee_goals(db, user.id)
    current_total = sum(g.weightage for g in goals)

    def error_response(msg):
        return templates.TemplateResponse(request, "employee/goals.html", {
            "user": user,
            "goals": goals,
            "total_weightage": current_total,
            "error": msg,
            "rework_comments": get_rework_comments(db, goals)
        })

    if len(goals) >= 8:
        return error_response("You cannot add more than 8 goals")

    if weightage < 10:
        return error_response("Each goal must have at least 10% weightage")

    if current_total + weightage > 100:
        return error_response(
            f"Cannot add goal. Current total is {current_total}%, adding {weightage}% would exceed 100%"
        )

    db.add(Goal(
        employee_id=user.id,
        title=title,
        thrust_area=thrust_area,
        uom_type=uom_type,
        target=target,
        weightage=weightage,
        status="draft"
    ))
    db.commit()
    return RedirectResponse(url="/employee/goals", status_code=302)


# ── Delete Goal ───────────────────────────────────────────

@router.post("/goals/delete/{goal_id}")
def delete_goal(goal_id: int, user=Depends(require_employee), db: Session = Depends(get_db)):
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.employee_id == user.id).first()
    if goal and goal.status == "draft":
        db.delete(goal)
        db.commit()
    return RedirectResponse(url="/employee/goals", status_code=302)


# ── Submit All Goals ──────────────────────────────────────

@router.post("/goals/submit")
def submit_goals(
    request: Request,
    user=Depends(require_employee),
    db: Session = Depends(get_db),
    acknowledged: str = Form(default="")
):
    goals = get_employee_goals(db, user.id)
    draft_goals = [g for g in goals if g.status == "draft"]
    rework_comments = get_rework_comments(db, goals)
    total_weightage = sum(g.weightage for g in goals)

    def error_response(msg):
        return templates.TemplateResponse(request, "employee/goals.html", {
            "user": user,
            "goals": goals,
            "total_weightage": total_weightage,
            "error": msg,
            "rework_comments": rework_comments
        })

    if not draft_goals:
        return error_response("No draft goals to submit")

    # If there are rework comments, employee must acknowledge them
    if rework_comments and acknowledged != "yes":
        return error_response("Please acknowledge the manager's feedback before resubmitting")

    try:
        validate_goals(goals)
    except Exception as e:
        return error_response(str(e.detail))

    # Clear rework comments on resubmit
    for goal in draft_goals:
        db.query(CheckIn).filter(
            CheckIn.goal_id == goal.id,
            CheckIn.quarter == "rework"
        ).delete()

    for goal in draft_goals:
        goal.status = "submitted"
    db.commit()

    return templates.TemplateResponse(request, "employee/goals.html", {
        "user": user,
        "goals": db.query(Goal).filter(Goal.employee_id == user.id).all(),
        "total_weightage": total_weightage,
        "success": "Goals submitted successfully! Waiting for manager approval.",
        "rework_comments": {}
    })


# ── Quarterly Check-in ────────────────────────────────────

@router.get("/checkin")
def checkin_page(request: Request, user=Depends(require_employee), db: Session = Depends(get_db)):
    goals = db.query(Goal).filter(
        Goal.employee_id == user.id,
        Goal.status == "locked"
    ).all()
    open_quarters = [
        w.quarter for w in db.query(CheckInWindow).filter(CheckInWindow.is_open == True).all()
    ]
    return templates.TemplateResponse(request, "employee/checkin.html", {
        "user": user,
        "goals": goals,
        "quarters": ["Q1", "Q2", "Q3", "Q4"],
        "open_quarters": open_quarters
    })


@router.post("/checkin/save")
def save_checkin(
    request: Request,
    user=Depends(require_employee),
    db: Session = Depends(get_db),
    goal_id: int = Form(...),
    quarter: str = Form(...),
    actual: float = Form(...),
    status: str = Form(...)
):
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.employee_id == user.id).first()
    if not goal or goal.status != "locked":
        return RedirectResponse(url="/employee/checkin", status_code=302)
    

    # Check window BEFORE saving anything
    window = db.query(CheckInWindow).filter(
        CheckInWindow.quarter == quarter,
        CheckInWindow.is_open == True
    ).first()
    if not window:
        return RedirectResponse(url="/employee/checkin?error=Check-in window for " + quarter + " is not open", status_code=302)

    achievement = db.query(Achievement).filter(
        Achievement.goal_id == goal_id,
        Achievement.quarter == quarter
    ).first()

    score = calculate_score(goal.uom_type, goal.target, actual)

    if achievement:
        achievement.actual = actual
        achievement.status = status
        achievement.score = score
    else:
        db.add(Achievement(
            goal_id=goal_id,
            quarter=quarter,
            actual=actual,
            status=status,
            score=score
        ))

    db.commit()
    return RedirectResponse(url="/employee/checkin", status_code=302)


# ── Reset (testing only) ──────────────────────────────────

@router.post("/goals/reset")
def reset_goals(user=Depends(require_employee), db: Session = Depends(get_db)):
    goals = db.query(Goal).filter(Goal.employee_id == user.id).all()
    for goal in goals:
        goal.status = "draft"
    db.commit()
    return RedirectResponse(url="/employee/goals", status_code=302)