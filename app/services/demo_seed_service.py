from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Achievement, AuditLog, CheckIn, CheckInWindow, Escalation, Goal, User
from app.services.goal_service import QUARTERS, calculate_score


ADMIN_EMAIL = "admin@test.com"
MANAGER_EMAIL = "manager@test.com"
EMPLOYEE_EMAIL = "employee@test.com"
SECONDARY_EMPLOYEE_EMAIL = "employee2@test.com"


def _get_user_by_email(db: Session, email: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise ValueError(f"Required account not found: {email}")
    return user


def _get_optional_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


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
) -> None:
    achievement = (
        db.query(Achievement)
        .filter(Achievement.goal_id == goal.id, Achievement.quarter == quarter)
        .first()
    )
    if not achievement:
        achievement = Achievement(goal_id=goal.id, quarter=quarter)
        db.add(achievement)

    achievement.actual = actual
    achievement.status = status
    achievement.score = calculate_score(goal.uom_type, goal.target, actual)
    achievement.updated_at = datetime.utcnow()


def _upsert_checkin(
    db: Session,
    *,
    goal_id: int,
    manager_id: int,
    quarter: str,
    comment: str,
) -> None:
    checkin = (
        db.query(CheckIn)
        .filter(CheckIn.goal_id == goal_id, CheckIn.manager_id == manager_id, CheckIn.quarter == quarter)
        .first()
    )
    if not checkin:
        db.add(CheckIn(goal_id=goal_id, manager_id=manager_id, quarter=quarter, comment=comment))
        return
    checkin.comment = comment


def _ensure_audit(
    db: Session,
    *,
    goal_id: int,
    changed_by: int,
    field: str,
    old_value: str | None,
    new_value: str | None,
) -> None:
    exists = (
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
    if exists:
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


def _prune_employee_goals(db: Session, *, employee_id: int, keep_titles: set[str]) -> int:
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
    employee2 = _get_optional_user_by_email(db, SECONDARY_EMPLOYEE_EMAIL)

    employee.manager_id = manager.id
    if employee2 and employee2.role == "employee":
        employee2.manager_id = manager.id

    keep_titles_primary = {
        "Revenue Growth by Enterprise Accounts",
        "Customer Satisfaction Score Improvement",
        "Launch Self-Service Support Portal",
        "Critical Production Incidents",
        "Department Shared KPI: Operational Efficiency (Owner)",
    }
    keep_titles_secondary = {
        "Department Shared KPI: Operational Efficiency",
        "Retention Rate Improvement for Managed Accounts",
        "Close High-Severity Support Tickets Within SLA",
    }
    removed_goals = _prune_employee_goals(db, employee_id=employee.id, keep_titles=keep_titles_primary)
    removed_goals_secondary = 0
    if employee2 and employee2.role == "employee":
        removed_goals_secondary = _prune_employee_goals(
            db, employee_id=employee2.id, keep_titles=keep_titles_secondary
        )

    # Keep quarter visibility deterministic for dashboards.
    for quarter in QUARTERS:
        window = db.query(CheckInWindow).filter(CheckInWindow.quarter == quarter).first()
        is_open = quarter == "Q1"
        if not window:
            db.add(CheckInWindow(quarter=quarter, is_open=is_open, opened_by=admin.id))
        else:
            window.is_open = is_open
            window.opened_by = admin.id
            window.updated_at = datetime.utcnow()

    # Shared KPI modeled as owner + recipient linkage across team members when available.
    shared_owner_goal, shared_owner_created = _upsert_goal(
        db,
        employee_id=employee.id,
        title="Department Shared KPI: Operational Efficiency (Owner)",
        thrust_area="Operational Efficiency",
        uom_type="min",
        target=12.0,
        weightage=15.0,
        status="locked",
        is_shared=True,
    )
    recipient_employee_id = employee2.id if employee2 and employee2.role == "employee" else employee.id
    shared_recipient_goal, shared_recipient_created = _upsert_goal(
        db,
        employee_id=recipient_employee_id,
        title="Department Shared KPI: Operational Efficiency",
        thrust_area="Operational Efficiency",
        uom_type="min",
        target=12.0,
        weightage=30.0,
        status="draft",
        is_shared=True,
        parent_goal_id=shared_owner_goal.id,
    )

    revenue_goal, revenue_created = _upsert_goal(
        db,
        employee_id=employee.id,
        title="Revenue Growth by Enterprise Accounts",
        thrust_area="Revenue Growth",
        uom_type="min",
        target=1200000.0,
        weightage=25.0,
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
    retention_goal = None
    retention_created = False
    sla_goal = None
    sla_created = False
    if employee2 and employee2.role == "employee":
        retention_goal, retention_created = _upsert_goal(
            db,
            employee_id=employee2.id,
            title="Retention Rate Improvement for Managed Accounts",
            thrust_area="Customer Success",
            uom_type="max",
            target=92.0,
            weightage=40.0,
            status="locked",
        )
        sla_goal, sla_created = _upsert_goal(
            db,
            employee_id=employee2.id,
            title="Close High-Severity Support Tickets Within SLA",
            thrust_area="Operational Excellence",
            uom_type="timeline",
            target=90.0,
            weightage=30.0,
            status="submitted",
        )

    # Manager inline-edit demo history on locked goal.
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

    # Employee quarter updates for completion and analytics.
    _upsert_achievement(db, goal=revenue_goal, quarter="Q1", actual=1185000.0, status="completed")
    _upsert_achievement(db, goal=revenue_goal, quarter="Q2", actual=0.0, status="not_started")
    _upsert_achievement(db, goal=csat_goal, quarter="Q1", actual=92.0, status="on_track")
    if retention_goal:
        _upsert_achievement(db, goal=retention_goal, quarter="Q1", actual=91.0, status="on_track")
    if shared_recipient_goal.employee_id != employee.id:
        _upsert_achievement(db, goal=shared_recipient_goal, quarter="Q1", actual=8.0, status="not_started")

    # Manager review history.
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
    if retention_goal:
        _upsert_checkin(
            db,
            goal_id=retention_goal.id,
            manager_id=manager.id,
            quarter="Q1",
            comment="Retention baseline is healthy. Expand upsell playbooks to improve stickiness.",
        )

    # Deterministic audit trail.
    for goal, was_created in [
        (revenue_goal, revenue_created),
        (csat_goal, csat_created),
        (timeline_goal, timeline_created),
        (zero_goal, zero_created),
        (shared_owner_goal, shared_owner_created),
        (shared_recipient_goal, shared_recipient_created),
        (retention_goal, retention_created),
        (sla_goal, sla_created),
    ]:
        if not goal:
            continue
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
        "secondary_employee_id": employee2.id if employee2 and employee2.role == "employee" else None,
        "manager_id": manager.id,
        "admin_id": admin.id,
        "removed_goals": removed_goals + removed_goals_secondary,
    }
