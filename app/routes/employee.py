from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Goal, Achievement, CheckIn
from app.auth import require_employee
from app.services.goal_service import (
    validate_goals,
    get_employee_goals,
    get_active_sheet_goals,
    calculate_score,
    enforce_goal_limits,
    get_open_checkin_quarters,
    is_goal_setting_open,
    is_shared_recipient_goal,
    sync_shared_achievement,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ── Dashboard ─────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(request: Request, user=Depends(require_employee)):
    return templates.TemplateResponse(request, "employee/dashboard.html", {"user": user})


# ── View Goals ────────────────────────────────────────────

def get_rework_feedback(db: Session, goals: list[Goal]) -> str:
    goal_ids = [g.id for g in goals]
    if not goal_ids:
        return ""
    latest = db.query(CheckIn).filter(
        CheckIn.goal_id.in_(goal_ids),
        CheckIn.quarter == "rework"
    ).order_by(CheckIn.created_at.desc()).first()
    return latest.comment if latest else ""


def get_rework_update_marks(db: Session, goals: list[Goal]) -> dict[int, str]:
    marks: dict[int, str] = {}
    for goal in goals:
        update_note = db.query(CheckIn).filter(
            CheckIn.goal_id == goal.id,
            CheckIn.quarter == "rework_update"
        ).order_by(CheckIn.created_at.desc()).first()
        if update_note and update_note.comment:
            marks[goal.id] = update_note.comment
    return marks


def render_goals_page(
    request: Request,
    user,
    db: Session,
    error: str = "",
    success: str = "",
    edit_goal_id: int | None = None,
):
    goals = get_employee_goals(db, user.id)
    active_goals = get_active_sheet_goals(goals)
    has_draft_goals = any(g.status == "draft" for g in active_goals)
    can_add_goal = (len(active_goals) == 0 or has_draft_goals) and is_goal_setting_open()
    rework_feedback = get_rework_feedback(db, active_goals)
    editable_goal = None
    if edit_goal_id is not None:
        editable_goal = next(
            (
                g for g in active_goals
                if g.id == edit_goal_id and g.status == "draft" and not is_shared_recipient_goal(g)
            ),
            None,
        )
    return templates.TemplateResponse(request, "employee/goals.html", {
        "user": user,
        "goals": goals,
        "active_total_weightage": sum(g.weightage for g in active_goals),
        "active_goals_count": len(active_goals),
        "has_draft_goals": has_draft_goals,
        "can_add_goal": can_add_goal,
        "rework_feedback": rework_feedback,
        "rework_updates": get_rework_update_marks(db, active_goals),
        "editable_goal": editable_goal,
        "error": error,
        "success": success,
        "goal_setting_open": is_goal_setting_open(),
    })


@router.get("/goals")
def view_goals(request: Request, user=Depends(require_employee), db: Session = Depends(get_db)):
    edit_goal_id = request.query_params.get("edit_goal_id")
    edit_goal = int(edit_goal_id) if edit_goal_id and edit_goal_id.isdigit() else None
    return render_goals_page(request, user, db, edit_goal_id=edit_goal)


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
    if not is_goal_setting_open():
        return render_goals_page(request, user, db, error="Goal editing is allowed only in the goal-setting window (May-June).")
    goals = get_active_sheet_goals(get_employee_goals(db, user.id))
    try:
        enforce_goal_limits(goals, weightage)
    except HTTPException as e:
        return render_goals_page(request, user, db, error=str(e.detail))

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
def delete_goal(goal_id: int, request: Request, user=Depends(require_employee), db: Session = Depends(get_db)):
    if not is_goal_setting_open():
        return render_goals_page(request, user, db, error="Goal editing is allowed only in the goal-setting window (May-June).")
    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.employee_id == user.id).first()
    if goal and goal.status == "draft" and not goal.is_shared:
        db.delete(goal)
        db.commit()
    elif goal and goal.is_shared:
        return render_goals_page(request, user, db, error="Shared goals cannot be deleted.")
    return RedirectResponse(url="/employee/goals", status_code=302)


# ── Submit All Goals ──────────────────────────────────────

@router.post("/goals/submit")
def submit_goals(
    request: Request,
    user=Depends(require_employee),
    db: Session = Depends(get_db),
    acknowledged: str = Form(default="")
):
    if not is_goal_setting_open():
        return render_goals_page(request, user, db, error="Goal submission is allowed only in the goal-setting window (May-June).")
    goals = get_employee_goals(db, user.id)
    active_goals = get_active_sheet_goals(goals)
    draft_goals = [g for g in active_goals if g.status == "draft"]
    rework_feedback = get_rework_feedback(db, active_goals)

    def error_response(msg):
        return render_goals_page(request, user, db, error=msg)

    if not draft_goals:
        return error_response("No draft goals to submit")

    # If there are rework comments, employee must acknowledge them
    if rework_feedback and acknowledged != "yes":
        return error_response("Please acknowledge the manager's feedback before resubmitting")

    try:
        validate_goals(draft_goals)
    except HTTPException as e:
        return error_response(str(e.detail))

    # Clear rework comments on resubmit
    for goal in draft_goals:
        db.query(CheckIn).filter(
            CheckIn.goal_id == goal.id,
            CheckIn.quarter == "rework"
        ).delete()
        db.query(CheckIn).filter(
            CheckIn.goal_id == goal.id,
            CheckIn.quarter == "rework_update"
        ).delete()

    for goal in draft_goals:
        goal.status = "submitted"
    db.commit()

    return render_goals_page(request, user, db, success="Goals submitted successfully! Waiting for manager approval.")


@router.post("/goals/shared-weightage/{goal_id}")
def update_shared_weightage(
    goal_id: int,
    request: Request,
    weightage: float = Form(...),
    user=Depends(require_employee),
    db: Session = Depends(get_db),
):
    if not is_goal_setting_open():
        return render_goals_page(request, user, db, error="Goal editing is allowed only in the goal-setting window (May-June).")

    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.employee_id == user.id).first()
    if not goal or goal.status != "draft" or not is_shared_recipient_goal(goal):
        return render_goals_page(request, user, db, error="Only draft recipient shared goals can be updated.")

    if weightage < 10:
        return render_goals_page(request, user, db, error="Each goal must have at least 10% weightage.")

    goals = get_active_sheet_goals(get_employee_goals(db, user.id))
    current_total = sum(g.weightage for g in goals) - goal.weightage
    if current_total + weightage > 100:
        return render_goals_page(
            request,
            user,
            db,
            error=f"Cannot update weightage. Current total without this goal is {current_total}%, setting {weightage}% exceeds 100%.",
        )

    goal.weightage = weightage
    db.commit()
    return RedirectResponse(url="/employee/goals", status_code=302)


@router.post("/goals/update/{goal_id}")
def update_goal(
    goal_id: int,
    request: Request,
    title: str = Form(...),
    thrust_area: str = Form(...),
    uom_type: str = Form(...),
    target: float = Form(...),
    weightage: float = Form(...),
    user=Depends(require_employee),
    db: Session = Depends(get_db),
):
    if not is_goal_setting_open():
        return render_goals_page(request, user, db, error="Goal editing is allowed only in the goal-setting window (May-June).")

    goal = db.query(Goal).filter(Goal.id == goal_id, Goal.employee_id == user.id).first()
    if not goal or goal.status != "draft":
        return render_goals_page(request, user, db, error="Only draft goals can be updated.")
    if is_shared_recipient_goal(goal):
        return render_goals_page(request, user, db, error="Shared recipient goals allow weightage-only edits.")
    if weightage < 10:
        return render_goals_page(request, user, db, error="Each goal must have at least 10% weightage.")

    active_goals = get_active_sheet_goals(get_employee_goals(db, user.id))
    current_total = sum(g.weightage for g in active_goals) - goal.weightage
    if current_total + weightage > 100:
        return render_goals_page(
            request,
            user,
            db,
            error=f"Cannot update weightage. Current total without this goal is {current_total}%, setting {weightage}% exceeds 100%.",
        )

    goal.title = title.strip()
    goal.thrust_area = thrust_area.strip()
    goal.uom_type = uom_type
    goal.target = target
    goal.weightage = weightage
    db.commit()
    return RedirectResponse(url="/employee/goals", status_code=302)


# ── Quarterly Check-in ────────────────────────────────────

@router.get("/checkin")
def checkin_page(request: Request, user=Depends(require_employee), db: Session = Depends(get_db)):
    goals = db.query(Goal).filter(
        Goal.employee_id == user.id,
        Goal.status == "locked"
    ).all()
    open_quarters = get_open_checkin_quarters(db)
    editable_goal_ids = {g.id for g in goals if not is_shared_recipient_goal(g)}
    return templates.TemplateResponse(request, "employee/checkin.html", {
        "user": user,
        "goals": goals,
        "quarters": ["Q1", "Q2", "Q3", "Q4"],
        "open_quarters": open_quarters,
        "editable_goal_ids": editable_goal_ids,
        "error": request.query_params.get("error"),
        "success": request.query_params.get("success"),
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
    if is_shared_recipient_goal(goal):
        return RedirectResponse(url="/employee/checkin?error=Shared goal achievements are synced from the primary owner.", status_code=302)

    if quarter not in get_open_checkin_quarters(db):
        return RedirectResponse(
            url="/employee/checkin?error=Check-in is allowed only in the active quarter window.",
            status_code=302,
        )

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

    if goal.is_shared and not goal.parent_goal_id:
        sync_shared_achievement(db, goal, quarter, actual, status, score)

    db.commit()
    return RedirectResponse(url="/employee/checkin?success=Check-in saved.", status_code=302)