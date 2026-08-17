from datetime import datetime
from typing import Optional, List


def new_assignment_doc(
    course_id: str,
    title: str,
    description: str,
    due_at,
    max_score: float,
    assignment_type: str,
    questions: list = None,
    attachments: list = None,  # New field
) -> dict:
    return {
        "courseId": course_id,
        "title": title,
        "description": description,
        "type": assignment_type,  # "assignment" | "quiz"
        "dueAt": due_at,
        "maxScore": max_score,
        "questions": questions or [],
        "attachments": attachments or [],  # New field
        "allowFileUpload": True,  # New field
        "createdAt": datetime.utcnow(),
    }


def assignment_to_public(doc: dict, include_answers: bool = False) -> dict:
    questions = doc.get("questions", [])
    if not include_answers:
        questions = [
            {"question": q["question"], "options": q["options"]} for q in questions
        ]
    return {
        "id": str(doc["_id"]),
        "courseId": doc["courseId"],
        "title": doc["title"],
        "description": doc.get("description", ""),
        "type": doc["type"],
        "dueAt": doc["dueAt"],
        "maxScore": doc["maxScore"],
        "questions": questions,
        "attachments": doc.get("attachments", []),  # New field
        "allowFileUpload": doc.get("allowFileUpload", True),
        "createdAt": doc["createdAt"],
    }
