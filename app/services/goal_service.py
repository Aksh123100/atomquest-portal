from sqlalchemy.orm import Session
from app.models import Goal, Achievement, AuditLog, User, CheckInWindow
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


def get_active_sheet_goals(goals: list[Goal]) -> list[Goal]:
    return [g for g in goals if g.status in {"draft", "submitted"}]


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


GOAL_SETTING_MONTHS = {5, 6}
QUARTER_MONTH_MAP = {
    "Q1": {7, 8, 9},
    "Q2": {10, 11, 12},
    "Q3": {1, 2},
    "Q4": {3, 4},
}
QUARTERS = ["Q1", "Q2", "Q3", "Q4"]


def get_cycle_phase(now: datetime | None = None) -> str:
    now = now or datetime.utcnow()
    month = now.month
    if month in GOAL_SETTING_MONTHS:
        return "goal_setting"
    for quarter, months in QUARTER_MONTH_MAP.items():
        if month in months:
            return quarter
    return "goal_setting"


def is_goal_setting_open(now: datetime | None = None) -> bool:
    return get_cycle_phase(now) == "goal_setting"


def get_scheduled_checkin_quarter(now: datetime | None = None) -> str | None:
    phase = get_cycle_phase(now)
    return phase if phase in QUARTERS else None


def get_open_checkin_quarters(db: Session, now: datetime | None = None) -> list[str]:
    windows = {
        w.quarter: w.is_open
        for w in db.query(CheckInWindow).all()
    }

    # Admin can explicitly open one or more quarters as an override.
    manual_open = [q for q in QUARTERS if windows.get(q) is True]
    if manual_open:
        return manual_open

    # Default schedule-driven behavior when no explicit open override exists.
    scheduled = get_scheduled_checkin_quarter(now)
    if not scheduled:
        return []
    is_open = windows.get(scheduled, True)
    return [scheduled] if is_open else []


def enforce_goal_limits(existing_goals: list[Goal], new_weightage: float):
    if len(existing_goals) >= 8:
        raise HTTPException(400, "You cannot add more than 8 goals")
    if new_weightage < 10:
        raise HTTPException(400, "Each goal must have at least 10% weightage")
    current_total = sum(g.weightage for g in existing_goals)
    if current_total + new_weightage > 100:
        raise HTTPException(
            400,
            f"Cannot add goal. Current total is {current_total}%, adding {new_weightage}% would exceed 100%",
        )


def is_shared_recipient_goal(goal: Goal) -> bool:
    return bool(goal.is_shared and goal.parent_goal_id)


def create_audit_log(
    db: Session,
    goal_id: int,
    changed_by: int,
    field: str,
    old_val,
    new_val,
):
    if str(old_val) == str(new_val):
        return
    db.add(
        AuditLog(
            goal_id=goal_id,
            changed_by=changed_by,
            field_changed=field,
            old_value=str(old_val) if old_val is not None else None,
            new_value=str(new_val) if new_val is not None else None,
            timestamp=datetime.utcnow(),
        )
    )


def goal_was_previously_locked(db: Session, goal_id: int) -> bool:
    return (
        db.query(AuditLog)
        .filter(
            AuditLog.goal_id == goal_id,
            AuditLog.field_changed == "status",
            AuditLog.old_value == "locked",
        )
        .first()
        is not None
    )


def create_shared_goal_bundle(
    db: Session,
    owner_id: int,
    recipient_ids: list[int],
    title: str,
    thrust_area: str,
    uom_type: str,
    target: float,
    owner_weightage: float,
    recipient_weightage: float,
):
    owner = db.query(User).filter(User.id == owner_id, User.role == "employee").first()
    if not owner:
        raise HTTPException(400, "Primary owner must be a valid employee")

    recipient_ids = sorted({rid for rid in recipient_ids if rid != owner_id})
    if not recipient_ids:
        raise HTTPException(400, "Select at least one recipient employee")

    recipients = db.query(User).filter(User.id.in_(recipient_ids), User.role == "employee").all()
    if len(recipients) != len(recipient_ids):
        raise HTTPException(400, "One or more recipients are invalid")

    owner_goals = get_active_sheet_goals(get_employee_goals(db, owner_id))
    enforce_goal_limits(owner_goals, owner_weightage)
    for recipient in recipients:
        recipient_goals = get_active_sheet_goals(get_employee_goals(db, recipient.id))
        enforce_goal_limits(recipient_goals, recipient_weightage)

    source_goal = Goal(
        employee_id=owner_id,
        title=title,
        thrust_area=thrust_area,
        uom_type=uom_type,
        target=target,
        weightage=owner_weightage,
        status="draft",
        is_shared=True,
    )
    db.add(source_goal)
    db.flush()

    for recipient in recipients:
        db.add(
            Goal(
                employee_id=recipient.id,
                title=title,
                thrust_area=thrust_area,
                uom_type=uom_type,
                target=target,
                weightage=recipient_weightage,
                status="draft",
                is_shared=True,
                parent_goal_id=source_goal.id,
            )
        )

    db.commit()
    return source_goal.id, len(recipients)


def sync_shared_achievement(
    db: Session,
    source_goal: Goal,
    quarter: str,
    actual: float,
    status: str,
    score: float,
):
    children = db.query(Goal).filter(Goal.parent_goal_id == source_goal.id).all()
    for child in children:
        achievement = db.query(Achievement).filter(
            Achievement.goal_id == child.id,
            Achievement.quarter == quarter,
        ).first()
        if achievement:
            achievement.actual = actual
            achievement.status = status
            achievement.score = score
            achievement.updated_at = datetime.utcnow()
        else:
            db.add(
                Achievement(
                    goal_id=child.id,
                    quarter=quarter,
                    actual=actual,
                    status=status,
                    score=score,
                )
            )