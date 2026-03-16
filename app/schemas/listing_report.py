from datetime import datetime

from pydantic import BaseModel, Field


class ListingReportCreateIn(BaseModel):
    reason: str = Field(..., min_length=1, max_length=64)
    details: str | None = Field(default=None, max_length=2000)


class ReportCreateIn(ListingReportCreateIn):
    listing_id: int


class ListingReportCreateOut(BaseModel):
    success: bool
    report_id: int
    status: str
    auto_hidden: bool


class ModerationActionOut(BaseModel):
    success: bool
    action: str
    listing_id: int
    updated_reports: int
    listing_status: str


class ModerationReportOut(BaseModel):
    id: int
    listing_id: int
    building_id: int
    reason: str
    details: str | None
    status: str
    created_at: str | None
    reporter_count: int
    action_taken: str | None
    available_actions: list[str]
    listing: dict
    seller: dict


class ModerationQueueOut(BaseModel):
    count: int
    reports: list[ModerationReportOut]


class ReportReviewIn(BaseModel):
    action: str = Field(..., min_length=1, max_length=32)
