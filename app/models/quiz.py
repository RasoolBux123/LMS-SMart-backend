from datetime import datetime
from typing import Optional, List


def new_quiz_doc(
    course_id: str,
    title: str,
    description: str = "",
    time_limit: int = 30,  # minutes
    passing_score: float = 60,  # percentage
    attempts_allowed: int = 1,
    is_published: bool = True,
    questions: List[dict] = None,
) -> dict:
    return {
        "courseId": course_id,
        "title": title,
        "description": description,
        "timeLimit": time_limit,
        "passingScore": passing_score,
        "attemptsAllowed": attempts_allowed,
        "isPublished": is_published,
        "questions": questions or [],
        "totalQuestions": len(questions or []),
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    }


def quiz_to_public(doc: dict, include_answers: bool = False) -> dict:
    questions = doc.get("questions", [])
    if not include_answers:
        questions = [
            {
                "id": q.get("id", i),
                "question": q["question"],
                "options": q["options"],
                "type": q.get("type", "single"),
                "points": q.get("points", 1),
            }
            for i, q in enumerate(questions)
        ]
    else:
        questions = [
            {
                "id": q.get("id", i),
                "question": q["question"],
                "options": q["options"],
                "type": q.get("type", "single"),
                "points": q.get("points", 1),
                "correctAnswer": q.get("correctAnswer"),
                "correctAnswers": q.get("correctAnswers", []),
            }
            for i, q in enumerate(questions)
        ]

    return {
        "id": str(doc["_id"]),
        "courseId": doc["courseId"],
        "title": doc["title"],
        "description": doc.get("description", ""),
        "timeLimit": doc.get("timeLimit", 30),
        "passingScore": doc.get("passingScore", 60),
        "attemptsAllowed": doc.get("attemptsAllowed", 1),
        "isPublished": doc.get("isPublished", True),
        "totalQuestions": doc.get("totalQuestions", 0),
        "questions": questions,
        "createdAt": doc["createdAt"],
        "updatedAt": doc.get("updatedAt", doc["createdAt"]),
    }


def quiz_attempt_doc(
    quiz_id: str,
    student_id: str,
    answers: List[dict] = None,
) -> dict:
    return {
        "quizId": quiz_id,
        "studentId": student_id,
        "answers": answers or [],
        "startedAt": datetime.utcnow(),
        "submittedAt": None,
        "score": None,
        "percentage": None,
        "passed": None,
        "feedback": None,
        "gradedBy": None,
        "gradedAt": None,
        "status": "in_progress",  # in_progress | submitted | graded
    }


def quiz_attempt_to_public(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "quizId": doc["quizId"],
        "studentId": doc["studentId"],
        "answers": doc.get("answers", []),
        "startedAt": doc["startedAt"],
        "submittedAt": doc.get("submittedAt"),
        "score": doc.get("score"),
        "percentage": doc.get("percentage"),
        "passed": doc.get("passed"),
        "feedback": doc.get("feedback"),
        "status": doc.get("status", "in_progress"),
    }
