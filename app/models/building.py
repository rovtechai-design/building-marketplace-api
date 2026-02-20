import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, func, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column

from app.models.base import Base


class Building(Base):
    __tablename__ = "buildings"
    __table_args__ = (UniqueConstraint("invite_code", name="uq_buildings_invite_code"),)

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    name = mapped_column(String(120), nullable=False)
    invite_code = mapped_column(String(32), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class BuildingMembership(Base):
    __tablename__ = "building_memberships"
    __table_args__ = (UniqueConstraint("user_id", "building_id", name="uq_memberships_user_building"),)

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    building_id = mapped_column(Integer, ForeignKey("buildings.id", ondelete="CASCADE"), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
