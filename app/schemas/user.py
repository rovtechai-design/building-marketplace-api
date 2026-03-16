from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MeUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    public_alias: str | None = Field(default=None, max_length=255)
    real_name: str | None = Field(default=None, max_length=255)
    full_name: str | None = Field(default=None, max_length=255)
    building_id: int | None = None
    room_number: str | None = Field(default=None, max_length=120)
    room_number_private: str | None = Field(default=None, max_length=120)
    profile_picture_url: str | None = Field(default=None, max_length=500)


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    firebase_uid: str
    email: str | None
    display_name: str | None
    public_alias: str | None
    real_name: str | None
    full_name: str | None
    building_id: int | None
    room_number: str | None
    room_number_private: str | None
    profile_picture_url: str | None
    role: str
    profile_completed: bool
    created_at: datetime
    updated_at: datetime | None
