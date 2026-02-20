import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, func, Integer, ForeignKey, Text, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column

from app.models.base import Base


class Listing(Base):
    __tablename__ = "listings"

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    title = mapped_column(String(140), nullable=False)
    description = mapped_column(Text, nullable=True)
    price = mapped_column(Numeric(10, 2), nullable=True)

    user_id = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    building_id = mapped_column(Integer, ForeignKey("buildings.id", ondelete="CASCADE"), nullable=False)

    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = mapped_column(DateTime(timezone=True), nullable=True)


class ListingImage(Base):
    __tablename__ = "listing_images"

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id = mapped_column(Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)
    image_url = mapped_column(Text, nullable=False)
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
