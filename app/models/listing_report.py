from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import mapped_column, relationship

from app.models.base import Base


class ListingReport(Base):
    __tablename__ = "listing_reports"
    __table_args__ = (
        UniqueConstraint("listing_id", "reporter_user_id", name="uq_listing_reports_listing_reporter"),
    )

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id = mapped_column(Integer, ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)
    reporter_user_id = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    reported_user_id = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    building_id = mapped_column(Integer, ForeignKey("buildings.id", ondelete="CASCADE"), nullable=False)
    reason = mapped_column(String(64), nullable=False)
    details = mapped_column(Text, nullable=True)
    status = mapped_column(String(32), nullable=False, server_default=text("'open'"))
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reviewed_at = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_user_id = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action_taken = mapped_column(String(64), nullable=True)

    listing = relationship("Listing", back_populates="reports")
