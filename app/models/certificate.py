"""Certificate documents live in the `certificates` collection."""

from __future__ import annotations

import random
import string
from datetime import datetime
from typing import Any, Optional

TEMPLATES = ("classic", "modern", "elegant")

# (min percentage, letter grade, remark printed on the certificate)
GRADE_BANDS = [
    (90, "A+", "Outstanding"),
    (85, "A", "Excellent"),
    (80, "A-", "Very Good"),
    (75, "B+", "Good"),
    (70, "B", "Good"),
    (65, "B-", "Satisfactory"),
    (60, "C+", "Satisfactory"),
    (55, "C", "Pass"),
    (50, "D", "Pass"),
]


def grade_for(percentage: float) -> tuple[str, str]:
    """Letter grade + one-word remark for a percentage."""
    for floor, letter, remark in GRADE_BANDS:
        if percentage >= floor:
            return letter, remark
    return "F", "Incomplete"


def generate_serial(year: Optional[int] = None) -> str:
    """Human-readable verification code, e.g. SLMS-2026-K7QX4M."""
    year = year or datetime.utcnow().year
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alike chars
    suffix = "".join(random.choice(alphabet) for _ in range(6))
    return f"SLMS-{year}-{suffix}"


def new_certificate_doc(
    *,
    serial: str,
    student_id: str,
    student_name: str,
    student_email: str,
    course_id: str,
    course_title: str,
    program_id: str = "",
    program_title: str = "",
    instructor_name: str = "",
    template: str = "classic",
    percentage: float = 0.0,
    grade: str = "",
    remark: str = "",
    total_marks: float = 0.0,
    obtained_marks: float = 0.0,
    duration_weeks: int = 0,
    completed_at: Optional[datetime] = None,
    issued_by: str = "",
    issued_by_name: str = "",
) -> dict:
    now = datetime.utcnow()
    return {
        "serial": serial,
        "studentId": student_id,
        "studentName": student_name,
        "studentEmail": student_email,
        "courseId": course_id,
        "courseTitle": course_title,
        "programId": program_id,
        "programTitle": program_title,
        "instructorName": instructor_name,
        "template": template if template in TEMPLATES else "classic",
        "percentage": round(float(percentage), 2),
        "grade": grade,
        "remark": remark,
        "totalMarks": total_marks,
        "obtainedMarks": obtained_marks,
        "durationWeeks": duration_weeks,
        "completedAt": completed_at or now,
        "status": "issued",
        "issuedAt": now,
        "issuedBy": issued_by,
        "issuedByName": issued_by_name,
        "revokedAt": None,
        "revokedReason": "",
        "filePath": "",
        "fileUrl": "",
        "createdAt": now,
        "updatedAt": now,
    }


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


def certificate_to_public(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "serial": doc.get("serial", ""),
        "studentId": doc.get("studentId", ""),
        "studentName": doc.get("studentName", ""),
        "studentEmail": doc.get("studentEmail", ""),
        "courseId": doc.get("courseId", ""),
        "courseTitle": doc.get("courseTitle", ""),
        "programId": doc.get("programId", ""),
        "programTitle": doc.get("programTitle", ""),
        "instructorName": doc.get("instructorName", ""),
        "template": doc.get("template", "classic"),
        "percentage": doc.get("percentage", 0),
        "grade": doc.get("grade", ""),
        "remark": doc.get("remark", ""),
        "totalMarks": doc.get("totalMarks", 0),
        "obtainedMarks": doc.get("obtainedMarks", 0),
        "durationWeeks": doc.get("durationWeeks", 0),
        "status": doc.get("status", "issued"),
        "completedAt": _iso(doc.get("completedAt")),
        "issuedAt": _iso(doc.get("issuedAt")),
        "issuedBy": doc.get("issuedBy", ""),
        "issuedByName": doc.get("issuedByName", ""),
        "revokedAt": _iso(doc.get("revokedAt")),
        "revokedReason": doc.get("revokedReason", ""),
        "fileUrl": doc.get("fileUrl", ""),
    }


def certificate_to_verification(doc: dict) -> dict:
    """Public payload — no ids, no email, nothing an outsider shouldn't see."""
    revoked = doc.get("status") == "revoked"
    return {
        "serial": doc.get("serial", ""),
        "valid": not revoked,
        "status": doc.get("status", "issued"),
        "studentName": doc.get("studentName", ""),
        "courseTitle": doc.get("courseTitle", ""),
        "programTitle": doc.get("programTitle", ""),
        "instructorName": doc.get("instructorName", ""),
        "grade": doc.get("grade", ""),
        "percentage": doc.get("percentage", 0),
        "issuedAt": _iso(doc.get("issuedAt")),
        "completedAt": _iso(doc.get("completedAt")),
        "revokedAt": _iso(doc.get("revokedAt")),
    }