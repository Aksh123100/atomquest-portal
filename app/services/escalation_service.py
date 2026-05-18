from sqlalchemy.orm import Session

from app.models import Achievement, CheckIn, Escalation, Goal, User
from app.services.notification_service import queue_email


def _find_escalation(
    db: Session,
    employee_id: int,
    manager_id: int | None,
    goal_id: int | None,
    quarter: str,
    escalation_type: str,
) -> Escalation | None:
    return (
        db.query(Escalation)
        .filter(
            Escalation.employee_id == employee_id,
            Escalation.manager_id == manager_id,
            Escalation.goal_id == goal_id,
            Escalation.quarter == quarter,
            Escalation.escalation_type == escalation_type,
        )
        .first()
    )


def _find_open_escalation(
    db: Session,
    employee_id: int,
    manager_id: int | None,
    goal_id: int | None,
    quarter: str,
    escalation_type: str,
) -> Escalation | None:
    return (
        db.query(Escalation)
        .filter(
            Escalation.employee_id == employee_id,
            Escalation.manager_id == manager_id,
            Escalation.goal_id == goal_id,
            Escalation.quarter == quarter,
            Escalation.escalation_type == escalation_type,
            Escalation.status == "open",
        )
        .first()
    )


def _create_escalation(
    db: Session,
    *,
    employee_id: int,
    manager_id: int | None,
    goal_id: int | None,
    quarter: str,
    escalation_type: str,
    reason: str,
) -> tuple[Escalation, bool]:
    existing = _find_escalation(db, employee_id, manager_id, goal_id, quarter, escalation_type)
    if existing:
        return existing, False
    escalation = Escalation(
        employee_id=employee_id,
        manager_id=manager_id,
        goal_id=goal_id,
        quarter=quarter,
        escalation_type=escalation_type,
        reason=reason,
        status="open",
    )
    db.add(escalation)
    db.flush()
    return escalation, True


def run_quarter_escalation_scan(db: Session, quarter: str) -> dict:
    created_employee_escalations = 0
    created_manager_escalations = 0
    auto_resolved_employee_escalations = 0
    auto_resolved_manager_escalations = 0

    locked_goals = db.query(Goal).filter(Goal.status == "locked").all()
    for goal in locked_goals:
        employee = db.query(User).filter(User.id == goal.employee_id).first()
        if not employee:
            continue

        achievement = db.query(Achievement).filter(
            Achievement.goal_id == goal.id,
            Achievement.quarter == quarter,
        ).first()
        if not achievement:
            escalation, created = _create_escalation(
                db,
                employee_id=employee.id,
                manager_id=employee.manager_id,
                goal_id=goal.id,
                quarter=quarter,
                escalation_type="employee_missed_checkin",
                reason=f"Employee check-in missing for goal '{goal.title}' in {quarter}.",
            )
            if created:
                created_employee_escalations += 1
                queue_email(
                    db,
                    recipient_user_id=employee.id,
                    subject=f"[AtomQuest] Escalation: Check-in missing for {quarter}",
                    body=f"Please complete your {quarter} check-in for goal '{goal.title}'.",
                    context_type="escalation",
                    context_id=escalation.id,
                )
                if employee.manager_id:
                    queue_email(
                        db,
                        recipient_user_id=employee.manager_id,
                        subject=f"[AtomQuest] Team escalation: {employee.name} missed check-in",
                        body=f"{employee.name} has not completed {quarter} check-in for goal '{goal.title}'.",
                        context_type="escalation",
                        context_id=escalation.id,
                    )
            continue

        open_employee_escalation = _find_open_escalation(
            db,
            employee_id=employee.id,
            manager_id=employee.manager_id,
            goal_id=goal.id,
            quarter=quarter,
            escalation_type="employee_missed_checkin",
        )
        if open_employee_escalation:
            open_employee_escalation.status = "resolved"
            open_employee_escalation.resolved_at = achievement.updated_at
            auto_resolved_employee_escalations += 1

        if employee.manager_id:
            manager_checkin = db.query(CheckIn).filter(
                CheckIn.goal_id == goal.id,
                CheckIn.quarter == quarter,
                CheckIn.manager_id == employee.manager_id,
            ).first()
            if not manager_checkin:
                escalation, created = _create_escalation(
                    db,
                    employee_id=employee.id,
                    manager_id=employee.manager_id,
                    goal_id=goal.id,
                    quarter=quarter,
                    escalation_type="manager_missed_review",
                    reason=f"Manager review comment missing for goal '{goal.title}' in {quarter}.",
                )
                if created:
                    created_manager_escalations += 1
                    queue_email(
                        db,
                        recipient_user_id=employee.manager_id,
                        subject=f"[AtomQuest] Escalation: Review comment pending for {quarter}",
                        body=f"Please add your check-in comment for {employee.name}'s goal '{goal.title}' ({quarter}).",
                        context_type="escalation",
                        context_id=escalation.id,
                    )
            else:
                open_manager_escalation = _find_open_escalation(
                    db,
                    employee_id=employee.id,
                    manager_id=employee.manager_id,
                    goal_id=goal.id,
                    quarter=quarter,
                    escalation_type="manager_missed_review",
                )
                if open_manager_escalation:
                    open_manager_escalation.status = "resolved"
                    open_manager_escalation.resolved_at = manager_checkin.created_at
                    auto_resolved_manager_escalations += 1

    db.commit()
    return {
        "quarter": quarter,
        "employee_missed_checkin_created": created_employee_escalations,
        "manager_missed_review_created": created_manager_escalations,
        "employee_missed_checkin_auto_resolved": auto_resolved_employee_escalations,
        "manager_missed_review_auto_resolved": auto_resolved_manager_escalations,
    }
