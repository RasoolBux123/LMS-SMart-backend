from datetime import datetime
from typing import Optional, List


def new_submission_doc(
    assignment_id: str,
    student_id: str,
    content: str = "",
    answers: list = None,
    files: list = None,
) -> dict:
    return {
        "assignmentId": assignment_id,
        "studentId": student_id,
        "content": content,
        "answers": answers or [],
        "files": files or [],
        "submittedAt": datetime.utcnow(),
        "score": None,
        "feedback": None,
        "gradedBy": None,
        "gradedAt": None,
        "status": "submitted",
        "attemptNumber": 1,
    }


def submission_to_public(doc: dict) -> dict:
    score = doc.get("score")
    status = doc.get("status")
    if status is None:
        status = "graded" if score is not None else "submitted"

    return {
        "id": str(doc["_id"]),
        "assignmentId": doc.get("assignmentId"),
        "studentId": doc.get("studentId"),
        "content": doc.get("content", ""),
        "answers": doc.get("answers", []),
        "files": doc.get("files", []),
        "submittedAt": doc.get("submittedAt"),
        "score": score,
        "marksAwarded": score,
        "feedback": doc.get("feedback"),
        "gradedAt": doc.get("gradedAt"),
        "status": status,
        "attemptNumber": doc.get("attemptNumber", 1),
        "passFail": (
            "pass" if score is not None and score >= 50 else
            "fail" if score is not None else None
        ),
    }


# from datetime import datetime


# def new_submission_doc(
#     assignment_id: str,
#     student_id: str,
#     content: str = "",
#     answers: list = None,
#     files: list = None,  # New field
# ) -> dict:
#     return {
#         "assignmentId": assignment_id,
#         "studentId": student_id,
#         "content": content,
#         "answers": answers or [],
#         "files": files or [],  # New field
#         "submittedAt": datetime.utcnow(),
#         "score": None,
#         "feedback": None,
#         "gradedBy": None,
#         "gradedAt": None,
#     }


# def submission_to_public(doc: dict) -> dict:
#     return {
#         "id": str(doc["_id"]),
#         "assignmentId": doc["assignmentId"],
#         "studentId": doc["studentId"],
#         "content": doc.get("content", ""),
#         "answers": doc.get("answers", []),
#         "files": doc.get("files", []),  # New field
#         "submittedAt": doc["submittedAt"],
#         "score": doc.get("score"),
#         "feedback": doc.get("feedback"),
#         "gradedAt": doc.get("gradedAt"),
#     }
