from pydantic import BaseModel
from typing import List, Literal


AttendanceStatus = Literal["present", "absent", "leave"]


class AttendanceItem(BaseModel):
    studentId: str
    status: AttendanceStatus


class MarkAttendanceRequest(BaseModel):
    courseId: str
    date: str
    attendance: List[AttendanceItem]