from datetime import datetime
from typing import Optional, List


def new_course_doc(
    title: str,
    description: str,
    instructor_id: str,
    category: str = "general",
    level: str = "beginner",
    duration_weeks: int = 4,
    thumbnail: str = "",
    objectives: List[str] = None,
    prerequisites: List[str] = None,
    status: str = "draft",
) -> dict:
    return {
        "title": title,
        "description": description,
        "instructorId": instructor_id,
        "category": category,
        "level": level,
        "durationWeeks": duration_weeks,
        "thumbnail": thumbnail,
        "objectives": objectives or [],
        "prerequisites": prerequisites or [],
        "status": status,
        "enrollmentCount": 0,
        "rating": 0,
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    }


def course_to_public(doc: dict) -> dict:
    created_at = doc.get("createdAt", datetime.utcnow())
    return {
        "id": str(doc["_id"]),
        "title": doc.get("title", "Untitled course"),
        "description": doc.get("description", ""),
        "instructorId": doc.get("instructorId", ""),
        "instructorName": doc.get("instructorName", ""),
        "category": doc.get("category", "general"),
        "level": doc.get("level", "beginner"),
        "durationWeeks": doc.get("durationWeeks", 4),
        "thumbnail": doc.get("thumbnail", ""),
        "objectives": doc.get("objectives", []),
        "prerequisites": doc.get("prerequisites", []),
        "status": doc.get("status", "draft"),
        "enrollmentCount": doc.get("enrollmentCount", 0),
        "rating": doc.get("rating", 0),
        "createdAt": created_at,
        "updatedAt": doc.get("updatedAt", created_at),
    }