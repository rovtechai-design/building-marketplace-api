import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("firebase_uid", name="uq_users_firebase_uid"),)

    id = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    firebase_uid = mapped_column(String(128), nullable=False)
    email = mapped_column(String(255), nullable=True)
    display_name = mapped_column(String(255), nullable=True)
    public_alias = mapped_column(String(255), nullable=True)
    full_name = mapped_column(String(255), nullable=True)
    building_id = mapped_column(Integer, ForeignKey("buildings.id", ondelete="SET NULL"), nullable=True)
    room_number_private = mapped_column(String(120), nullable=True)
    profile_picture_url = mapped_column(String(500), nullable=True)
    role = mapped_column(String(32), nullable=False, server_default=text("'user'"))
    profile_completed = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
