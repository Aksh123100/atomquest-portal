from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Text, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    role = Column(String, nullable=False)  # employee / manager / admin
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    manager = relationship("User", remote_side=[id], backref="team")
    goals = relationship("Goal", back_populates="employee")


class Goal(Base):
    __tablename__ = "goals"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, nullable=False)
    thrust_area = Column(String, nullable=False)
    uom_type = Column(String, nullable=False)  # min / max / zero / timeline
    target = Column(Float, nullable=False)
    weightage = Column(Float, nullable=False)
    status = Column(String, default="draft")  # draft / submitted / approved / locked
    is_shared = Column(Boolean, default=False)
    parent_goal_id = Column(Integer, ForeignKey("goals.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    employee = relationship("User", back_populates="goals")
    achievements = relationship("Achievement", back_populates="goal")
    audit_logs = relationship("AuditLog", back_populates="goal")


class Achievement(Base):
    __tablename__ = "achievements"

    id = Column(Integer, primary_key=True, index=True)
    goal_id = Column(Integer, ForeignKey("goals.id"), nullable=False)
    quarter = Column(String, nullable=False)  # Q1 / Q2 / Q3 / Q4
    actual = Column(Float, nullable=True)
    score = Column(Float, nullable=True)
    status = Column(String, default="not_started")  # not_started / on_track / completed
    updated_at = Column(DateTime, default=datetime.utcnow)

    goal = relationship("Goal", back_populates="achievements")


class CheckIn(Base):
    __tablename__ = "checkins"

    id = Column(Integer, primary_key=True, index=True)
    goal_id = Column(Integer, ForeignKey("goals.id"), nullable=False)
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    quarter = Column(String, nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    goal_id = Column(Integer, ForeignKey("goals.id"), nullable=False)
    changed_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    field_changed = Column(String, nullable=False)
    old_value = Column(String, nullable=True)
    new_value = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)

    goal = relationship("Goal", back_populates="audit_logs")

class CheckInWindow(Base):
    __tablename__ = "checkin_windows"

    id = Column(Integer, primary_key=True, index=True)
    quarter = Column(String, nullable=False)  # Q1 / Q2 / Q3 / Q4
    is_open = Column(Boolean, default=False)
    opened_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)