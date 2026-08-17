from datetime import datetime


def new_project_doc(
    course_id: str,
    title: str,
    description: str,
    due_at,
    max_score: float,
    attachment_url: str | None = None,
    instructions: str = "",
    max_file_size_mb: int = 25,
    allowed_file_types: list[str] | None = None,
    status: str = "draft",
) -> dict:
    return {
        "courseId": course_id,
        "title": title,
        "description": description,
        "instructions": instructions,
        "dueAt": due_at,
        "maxScore": max_score,
        "maxFileSizeMb": max_file_size_mb,
        "allowedFileTypes": allowed_file_types or [],
        "status": status,
        "attachmentUrl": attachment_url,  # file uploaded by instructor
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    }


def project_to_public(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "courseId": doc["courseId"],
        "title": doc["title"],
        "description": doc.get("description", ""),
        "instructions": doc.get("instructions", ""),
        "dueAt": doc["dueAt"],
        "maxScore": doc["maxScore"],
        "maxFileSizeMb": doc.get("maxFileSizeMb", 25),
        "allowedFileTypes": doc.get("allowedFileTypes", []),
        "status": doc.get("status", "draft"),
        "attachmentUrl": doc.get("attachmentUrl"),
        "createdAt": doc.get("createdAt"),
        "updatedAt": doc.get("updatedAt"),
    }