from sqlalchemy.orm import Session
from app.models import Goal, Achievement, AuditLog
from fastapi import HTTPException
from datetime import datetime


# ── Validation ────────────────────────────────────────────

def validate_goals(goals: list):
    """
    Validates all goals before submission.
    Rules:
    - Max 8 goals
    - Each goal min 10% weightage
    - Total weightage must equal 100%
    """
    if len(goals) > 8:
        raise HTTPException(400, "You cannot have more than 8 goals")

    for goal in goals:
        if goal.weightage < 10:
            raise HTTPException(400, f"Goal '{goal.title}' must have at least 10% weightage")

    total = sum(g.weightage for g in goals)
    if round(total) != 100:
        raise HTTPException(400, f"Total weightage must equal 100%. Current total: {total}%")


# ── Score Calculation ─────────────────────────────────────

def calculate_score(uom_type: str, target: float, actual: float) -> float:
    """
    Calculates progress score based on goal type.
    Returns a float between 0 and 1 (multiply by 100 for percentage).
    """
    if target == 0:
        return 0.0

    if uom_type == "min":
        # Higher is better e.g. sales revenue
        # Achievement / Target
        score = actual / target

    elif uom_type == "max":
        # Lower is better e.g. cost, TAT
        # Target / Achievement
        if actual == 0:
            return 1.0  # achieved zero cost = perfect
        score = target / actual

    elif uom_type == "zero":
        # Zero = success e.g. safety incidents
        score = 1.0 if actual == 0 else 0.0

    elif uom_type == "timeline":
        # actual = days taken, target = days allowed
        if actual <= target:
            score = 1.0  # finished on time
        else:
            score = target / actual  # late, partial score

    else:
        score = 0.0

    # Cap between 0 and 1
    return round(min(max(score, 0.0), 1.0), 2)


# ── Goal Operations ───────────────────────────────────────

def get_employee_goals(db: Session, employee_id: int):
    return db.query(Goal).filter(Goal.employee_id == employee_id).all()


def get_submitted_goals(db: Session, manager_id: int):
    """Get all submitted goals for employees under this manager."""
    from app.models import User
    team = db.query(User).filter(User.manager_id == manager_id).all()
    team_ids = [u.id for u in team]
    return db.query(Goal).filter(
        Goal.employee_id.in_(team_ids),
        Goal.status == "submitted"
    ).all()


def lock_goal(db: Session, goal_id: int, manager_id: int):
    """Manager approves a goal — locks it."""
    goal = db.query(Goal).filter(Goal.id == goal_id).first()
    if not goal:
        raise HTTPException(404, "Goal not found")
    if goal.status != "submitted":
        raise HTTPException(400, "Goal is not in submitted state")

    goal.status = "locked"
    db.commit()
    return goal


def log_audit(db: Session, goal_id: int, changed_by: int, field: str, old_val: str, new_val: str):
    """Log a change to a locked goal."""
    entry = AuditLog(
        goal_id=goal_id,
        changed_by=changed_by,
        field_changed=field,
        old_value=old_val,
        new_value=new_val,
        timestamp=datetime.utcnow()
    )
    db.add(entry)
    db.commit()