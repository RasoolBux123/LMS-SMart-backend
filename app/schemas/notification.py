# app/schemas/notification.py
from pydantic import BaseModel
from typing import Optional, Literal
from datetime import datetime

NotificationType = Literal["risk_alert", "deadline_reminder", "announcement", "grade_posted"]
NotificationSeverity = Literal["info", "warning", "critical"]


class NotificationOut(BaseModel):
    id: str
    user_id: str          # recipient (student email or instructor email)
    type: NotificationType
    severity: NotificationSeverity
    title: str
    message: str
    link: Optional[str] = None       # e.g. "/instructor/ai-insights" or "/student/assignments/123"
    course_id: Optional[str] = None
    read: bool = False
    created_at: str


class MarkReadRequest(BaseModel):
    notification_ids: list[str]