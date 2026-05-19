from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Achievement, AuditLog, CheckIn, CheckInWindow, Escalation, Goal, User
from app.services.goal_service import QUARTERS, calculate_score


ADMIN_EMAIL = "admin@test.com"
MANAGER_EMAIL = "manager@test.com"
EMPLOYEE_EMAIL = "employee@test.com"


def _get_user_by_email(db: Session, email: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise ValueError(f"Required account not found: {email}")
    return user


def _upsert_goal(
    db: Session,
    *,
    employee_id: int,
    title: str,
    thrust_area: str,
    uom_type: str,
    target: float,
    weightage: float,
    status: str,
    is_shared: bool = False,
    parent_goal_id: int | None = None,
) -> tuple[Goal, bool]:
    goal = (
        db.query(Goal)
        .filter(Goal.employee_id == employee_id, Goal.title == title)
        .order_by(Goal.id.asc())
        .first()
    )
    created = goal is None
    if not goal:
        goal = Goal(employee_id=employee_id, title=title)
        db.add(goal)

    goal.thrust_area = thrust_area
    goal.uom_type = uom_type
    goal.target = target
    goal.weightage = weightage
    goal.status = status
    goal.is_shared = is_shared
    goal.parent_goal_id = parent_goal_id
    db.flush()
    return goal, created


def _upsert_achievement(
    db: Session,
    *,
    goal: Goal,
    quarter: str,
    actual: float,
    status: str,
) -> Achievement:
    achievement = (
        db.query(Achievement)
        .filter(Achievement.goal_id == goal.id, Achievement.quarter == quarter)
        .first()
    )
    score = calculate_score(goal.uom_type, goal.target, actual)
    if not achievement:
        achievement = Achievement(goal_id=goal.id, quarter=quarter)
        db.add(achievement)

    achievement.actual = actual
    achievement.status = status
    achievement.score = score
    achievement.updated_at = datetime.utcnow()
    return achievement


def _upsert_checkin(
    db: Session,
    *,
    goal_id: int,
    manager_id: int,
    quarter: str,
    comment: str,
) -> CheckIn:
    checkin = (
        db.query(CheckIn)
        .filter(CheckIn.goal_id == goal_id, CheckIn.manager_id == manager_id, CheckIn.quarter == quarter)
        .first()
    )
    if not checkin:
        checkin = CheckIn(goal_id=goal_id, manager_id=manager_id, quarter=quarter, comment=comment)
        db.add(checkin)
    else:
        checkin.comment = comment
    return checkin


def _ensure_audit(
    db: Session,
    *,
    goal_id: int,
    changed_by: int,
    field: str,
    old_value: str | None,
    new_value: str | None,
) -> None:
    existing = (
        db.query(AuditLog)
        .filter(
            AuditLog.goal_id == goal_id,
            AuditLog.changed_by == changed_by,
            AuditLog.field_changed == field,
            AuditLog.old_value == old_value,
            AuditLog.new_value == new_value,
        )
        .first()
    )
    if existing:
        return
    db.add(
        AuditLog(
            goal_id=goal_id,
            changed_by=changed_by,
            field_changed=field,
            old_value=old_value,
            new_value=new_value,
            timestamp=datetime.utcnow(),
        )
    )


def _prune_goals(db: Session, *, employee_id: int, keep_titles: set[str]) -> int:
    stale_goals = (
        db.query(Goal)
        .filter(Goal.employee_id == employee_id, ~Goal.title.in_(keep_titles))
        .all()
    )
    if not stale_goals:
        return 0

    stale_goal_ids = [g.id for g in stale_goals]
    db.query(Achievement).filter(Achievement.goal_id.in_(stale_goal_ids)).delete(synchronize_session=False)
    db.query(CheckIn).filter(CheckIn.goal_id.in_(stale_goal_ids)).delete(synchronize_session=False)
    db.query(AuditLog).filter(AuditLog.goal_id.in_(stale_goal_ids)).delete(synchronize_session=False)
    db.query(Escalation).filter(Escalation.goal_id.in_(stale_goal_ids)).update(
        {Escalation.goal_id: None},
        synchronize_session=False,
    )
    db.query(Goal).filter(Goal.id.in_(stale_goal_ids)).delete(synchronize_session=False)
    return len(stale_goal_ids)


def seed_demo_data(db: Session) -> dict:
    admin = _get_user_by_email(db, ADMIN_EMAIL)
    manager = _get_user_by_email(db, MANAGER_EMAIL)
    employee = _get_user_by_email(db, EMPLOYEE_EMAIL)

    all_employees = db.query(User).filter(User.role == "employee").order_by(User.id.asc()).all()
    if len(all_employees) < 2:
        raise ValueError("At least two employee accounts are required to demonstrate shared KPI behavior.")

    secondary_employee = next((u for u in all_employees if u.id != employee.id), None)
    if not secondary_employee:
        raise ValueError("Unable to find a second employee account for shared KPI seeding.")

    employee_keep_titles = {
        "Revenue Growth by Enterprise Accounts",
        "Customer Satisfaction Score Improvement",
        "Launch Self-Service Support Portal",
        "Critical Production Incidents",
        "Department Shared KPI: Operational Efficiency",
    }
    secondary_keep_titles = {
        "Department Shared KPI: Operational Efficiency",
        "Regional Sales Win Rate",
        "Reduce Sales Cycle Time",
    }
    removed_employee_goals = _prune_goals(db, employee_id=employee.id, keep_titles=employee_keep_titles)
    removed_secondary_goals = _prune_goals(db, employee_id=secondary_employee.id, keep_titles=secondary_keep_titles)

    # Ensure team mapping for manager dashboard metrics.
    for member in (employee, secondary_employee):
        member.manager_id = manager.id

    # Keep demo quarter behavior deterministic: Q1 open, Q2-Q4 closed.
    for quarter in QUARTERS:
        window = db.query(CheckInWindow).filter(CheckInWindow.quarter == quarter).first()
        is_open = quarter == "Q1"
        if not window:
            db.add(CheckInWindow(quarter=quarter, is_open=is_open, opened_by=admin.id))
        else:
            window.is_open = is_open
            window.opened_by = admin.id
            window.updated_at = datetime.utcnow()

    # Shared KPI owner goal on another employee, recipient on demo employee (weightage-editable draft).
    shared_owner_goal, shared_owner_created = _upsert_goal(
        db,
        employee_id=secondary_employee.id,
        title="Department Shared KPI: Operational Efficiency",
        thrust_area="Operational Efficiency",
        uom_type="min",
        target=12.0,
        weightage=25.0,
        status="submitted",
        is_shared=True,
    )
    shared_recipient_goal, shared_recipient_created = _upsert_goal(
        db,
        employee_id=employee.id,
        title="Department Shared KPI: Operational Efficiency",
        thrust_area="Operational Efficiency",
        uom_type="min",
        target=12.0,
        weightage=30.0,
        status="draft",
        is_shared=True,
        parent_goal_id=shared_owner_goal.id,
    )

    # Employee demo sheet (5 goals total; active draft+submitted = 100).
    revenue_goal, revenue_created = _upsert_goal(
        db,
        employee_id=employee.id,
        title="Revenue Growth by Enterprise Accounts",
        thrust_area="Revenue Growth",
        uom_type="min",
        target=1200000.0,
        weightage=20.0,
        status="locked",
    )
    csat_goal, csat_created = _upsert_goal(
        db,
        employee_id=employee.id,
        title="Customer Satisfaction Score Improvement",
        thrust_area="Customer Experience",
        uom_type="min",
        target=95.0,
        weightage=20.0,
        status="locked",
    )
    timeline_goal, timeline_created = _upsert_goal(
        db,
        employee_id=employee.id,
        title="Launch Self-Service Support Portal",
        thrust_area="Digital Transformation",
        uom_type="timeline",
        target=90.0,
        weightage=40.0,
        status="submitted",
    )
    zero_goal, zero_created = _upsert_goal(
        db,
        employee_id=employee.id,
        title="Critical Production Incidents",
        thrust_area="Reliability",
        uom_type="zero",
        target=0.0,
        weightage=30.0,
        status="draft",
    )

    # Secondary employee goals so manager dashboard has richer team state.
    secondary_locked_goal, secondary_locked_created = _upsert_goal(
        db,
        employee_id=secondary_employee.id,
        title="Regional Sales Win Rate",
        thrust_area="Sales Effectiveness",
        uom_type="min",
        target=45.0,
        weightage=50.0,
        status="locked",
    )
    secondary_submitted_goal, secondary_submitted_created = _upsert_goal(
        db,
        employee_id=secondary_employee.id,
        title="Reduce Sales Cycle Time",
        thrust_area="Operational Efficiency",
        uom_type="max",
        target=35.0,
        weightage=50.0,
        status="submitted",
    )

    # Simulate manager inline edits on a locked goal.
    previous_target = revenue_goal.target
    previous_weightage = revenue_goal.weightage
    revenue_goal.target = 1150000.0
    revenue_goal.weightage = 25.0
    _ensure_audit(
        db,
        goal_id=revenue_goal.id,
        changed_by=manager.id,
        field="target",
        old_value=str(previous_target),
        new_value=str(revenue_goal.target),
    )
    _ensure_audit(
        db,
        goal_id=revenue_goal.id,
        changed_by=manager.id,
        field="weightage",
        old_value=str(previous_weightage),
        new_value=str(revenue_goal.weightage),
    )

    # Quarterly achievements with mixed statuses.
    _upsert_achievement(db, goal=revenue_goal, quarter="Q1", actual=1185000.0, status="completed")
    _upsert_achievement(db, goal=revenue_goal, quarter="Q2", actual=0.0, status="not_started")
    _upsert_achievement(db, goal=csat_goal, quarter="Q1", actual=92.0, status="on_track")
    _upsert_achievement(db, goal=secondary_locked_goal, quarter="Q1", actual=41.0, status="on_track")

    # Manager review comments history.
    _upsert_checkin(
        db,
        goal_id=revenue_goal.id,
        manager_id=manager.id,
        quarter="Q1",
        comment="Strong quarter close. Improve pipeline conversion in enterprise vertical.",
    )
    _upsert_checkin(
        db,
        goal_id=csat_goal.id,
        manager_id=manager.id,
        quarter="Q1",
        comment="Customer feedback trend improved. Focus on response SLA consistency.",
    )
    _upsert_checkin(
        db,
        goal_id=secondary_locked_goal.id,
        manager_id=manager.id,
        quarter="Q1",
        comment="Progress is visible, but lagging in the west region opportunities.",
    )

    # Deterministic audit trail for demo narrative.
    created_goal_flags = [
        (revenue_goal, revenue_created),
        (csat_goal, csat_created),
        (timeline_goal, timeline_created),
        (zero_goal, zero_created),
        (shared_owner_goal, shared_owner_created),
        (shared_recipient_goal, shared_recipient_created),
        (secondary_locked_goal, secondary_locked_created),
        (secondary_submitted_goal, secondary_submitted_created),
    ]
    for goal, was_created in created_goal_flags:
        if was_created:
            _ensure_audit(
                db,
                goal_id=goal.id,
                changed_by=goal.employee_id,
                field="goal_created",
                old_value=None,
                new_value=goal.title,
            )

    _ensure_audit(
        db,
        goal_id=timeline_goal.id,
        changed_by=employee.id,
        field="status",
        old_value="draft",
        new_value="submitted",
    )
    _ensure_audit(
        db,
        goal_id=revenue_goal.id,
        changed_by=manager.id,
        field="status",
        old_value="submitted",
        new_value="locked",
    )
    _ensure_audit(
        db,
        goal_id=csat_goal.id,
        changed_by=manager.id,
        field="status",
        old_value="submitted",
        new_value="locked",
    )
    _ensure_audit(
        db,
        goal_id=csat_goal.id,
        changed_by=admin.id,
        field="status",
        old_value="locked",
        new_value="draft",
    )
    _ensure_audit(
        db,
        goal_id=csat_goal.id,
        changed_by=admin.id,
        field="status",
        old_value="draft",
        new_value="locked",
    )
    _ensure_audit(
        db,
        goal_id=shared_recipient_goal.id,
        changed_by=manager.id,
        field="shared_goal_linked",
        old_value=None,
        new_value=str(shared_owner_goal.id),
    )
    _ensure_audit(
        db,
        goal_id=revenue_goal.id,
        changed_by=employee.id,
        field="achievement_Q1",
        old_value="on_track",
        new_value="completed",
    )
    _ensure_audit(
        db,
        goal_id=csat_goal.id,
        changed_by=employee.id,
        field="achievement_Q1",
        old_value="not_started",
        new_value="on_track",
    )

    db.commit()
    return {
        "employee_id": employee.id,
        "secondary_employee_id": secondary_employee.id,
        "manager_id": manager.id,
        "admin_id": admin.id,
        "removed_employee_goals": removed_employee_goals,
        "removed_secondary_goals": removed_secondary_goals,
    }
