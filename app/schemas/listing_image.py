from datetime import datetime

from pydantic import BaseModel, ConfigDict, HttpUrl


class ListingImageCreateIn(BaseModel):
    image_url: HttpUrl


class ListingImageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_id: int
    image_url: str
    created_at: datetime
