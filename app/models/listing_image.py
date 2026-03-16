from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime

from .base import Base


class ListingImage(Base):
    __tablename__ = "listing_images"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"), nullable=False)

    image_url: Mapped[str] = mapped_column(nullable=False)

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    listing = relationship("Listing", back_populates="images")
