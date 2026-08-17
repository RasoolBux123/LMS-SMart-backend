from datetime import datetime
from typing import List, Optional


def new_program_doc(
    code: str,
    title: str,
    description: str = "",
    level: str = "diploma",
    status: str = "draft",
    duration_months: int = 12,
    total_credits: int = 0,
    coordinator: str = "",
    company: str = "",
    course_ids: Optional[List[str]] = None,
    color: str = "",
) -> dict:
    now = datetime.utcnow()
    return {
        "code": code.strip().upper(),
        "title": title.strip(),
        "description": description or "",
        "level": level,
        "status": status,
        "durationMonths": duration_months,
        "totalCredits": total_credits,
        "coordinator": coordinator or "",
        "company": company or "",
        "courseIds": course_ids or [],
        "color": color or "",
        "createdAt": now,
        "updatedAt": now,
    }


def program_to_public(doc: dict, course_count: int = None, student_count: int = None) -> dict:
    created_at = doc.get("createdAt", datetime.utcnow())
    course_ids = doc.get("courseIds") or []
    return {
        "id": str(doc["_id"]),
        "code": doc.get("code", ""),
        "title": doc.get("title", "Untitled program"),
        "description": doc.get("description", ""),
        "level": doc.get("level", "diploma"),
        "status": doc.get("status", "draft"),
        "durationMonths": doc.get("durationMonths", 12),
        "totalCredits": doc.get("totalCredits", 0),
        "coordinator": doc.get("coordinator", ""),
        "company": doc.get("company", ""),
        "courseIds": course_ids,
        "courseCount": course_count if course_count is not None else len(course_ids),
        "studentCount": student_count if student_count is not None else 0,
        "color": doc.get("color", ""),
        "createdAt": created_at,
        "updatedAt": doc.get("updatedAt", created_at),
    }

# def program_to_public(doc: dict, course_count: int = None, student_count: int = None) -> dict:
#     created_at = doc.get("createdAt", datetime.utcnow())
#     course_ids = doc.get("courseIds") or []
#     return {
#         "id": str(doc["_id"]),
#         "code": doc.get("code", ""),
#         "title": doc.get("title", "Untitled program"),
#         "description": doc.get("description", ""),
#         "level": doc.get("level", "diploma"),
#         "status": doc.get("status", "draft"),
#         "durationMonths": doc.get("durationMonths", 12),
#         "totalCredits": doc.get("totalCredits", 0),
#         "coordinator": doc.get("coordinator", ""),
#         "courseIds": course_ids,
#         "courseCount": course_count if course_count is not None else len(course_ids),
#         "studentCount": student_count if student_count is not None else 0,
#         "color": doc.get("color", ""),
#         "createdAt": created_at,
#         "updatedAt": doc.get("updatedAt", created_at),
#     }