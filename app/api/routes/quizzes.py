from fastapi import APIRouter, Depends, HTTPException, Query
from bson import ObjectId
from datetime import datetime
from typing import Optional
import math

from app.core.database import database
from app.api.deps import require_roles, get_current_user
from app.models.quiz import (
    new_quiz_doc,
    quiz_to_public,
    quiz_attempt_doc,
    quiz_attempt_to_public,
)
from app.schemas.quiz import (
    CreateQuizRequest,
    UpdateQuizRequest,
    SubmitQuizRequest,
    GradeQuizRequest,
    QuizListParams,
)

router = APIRouter(prefix="/quizzes", tags=["quizzes"])


# ========== CREATE QUIZ ==========
@router.post("")
async def create_quiz(
    payload: CreateQuizRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    """Create a new quiz"""
    db = database.db

    # Verify course exists
    course = await db.courses.find_one({"_id": ObjectId(payload.courseId)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    # Check instructor owns this course
    if user["role"] == "instructor":
        instructor_id = course.get("instructorId")
        if not instructor_id or instructor_id != str(user["_id"]):
            raise HTTPException(status_code=403, detail="You don't own this course")

    # Prepare questions
    questions = []
    for i, q in enumerate(payload.questions):
        question_data = {
            "id": i,
            "question": q.question,
            "options": q.options,
            "type": q.type,
            "points": q.points,
        }
        if q.type == "single":
            question_data["correctAnswer"] = q.correctAnswer
        else:
            question_data["correctAnswers"] = q.correctAnswers or []
        questions.append(question_data)

    doc = new_quiz_doc(
        course_id=payload.courseId,
        title=payload.title,
        description=payload.description,
        time_limit=payload.timeLimit,
        passing_score=payload.passingScore,
        attempts_allowed=payload.attemptsAllowed,
        is_published=payload.isPublished,
        questions=questions,
    )

    result = await db.quizzes.insert_one(doc)
    doc["_id"] = result.inserted_id

    return {
        "success": True,
        "data": quiz_to_public(doc, include_answers=True),
        "message": "Quiz created successfully",
    }


# ========== LIST QUIZZES ==========
@router.get("")
async def list_quizzes(
    course_id: Optional[str] = Query(None),
    is_published: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """List all quizzes with filters"""
    db = database.db

    query = {}
    if course_id:
        query["courseId"] = course_id
    if is_published is not None:
        query["isPublished"] = is_published

    # If student, only show published quizzes from enrolled courses
    if user["role"] == "student":
        enrollments = await db.enrollments.find({"userId": str(user["_id"])}).to_list(
            length=None
        )
        course_ids = [e["courseId"] for e in enrollments]
        query["courseId"] = {"$in": course_ids}
        query["isPublished"] = True

    skip = (page - 1) * limit
    total = await db.quizzes.count_documents(query)

    cursor = db.quizzes.find(query).sort("createdAt", -1).skip(skip).limit(limit)
    include_answers = user["role"] in ("instructor", "admin")
    quizzes = [quiz_to_public(q, include_answers) async for q in cursor]

    return {
        "success": True,
        "data": {
            "quizzes": quizzes,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": math.ceil(total / limit) if total > 0 else 0,
            },
        },
        "message": "ok",
    }


# ========== GET QUIZ ==========
@router.get("/{quiz_id}")
async def get_quiz(quiz_id: str, user: dict = Depends(get_current_user)):
    """Get quiz details"""
    db = database.db

    if not ObjectId.is_valid(quiz_id):
        raise HTTPException(status_code=400, detail="Invalid quiz ID")

    quiz = await db.quizzes.find_one({"_id": ObjectId(quiz_id)})
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    # Check access
    if user["role"] == "student":
        course = await db.courses.find_one({"_id": ObjectId(quiz["courseId"])})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

        enrollment = await db.enrollments.find_one(
            {"courseId": quiz["courseId"], "userId": str(user["_id"])}
        )
        if not enrollment:
            raise HTTPException(
                status_code=403, detail="You are not enrolled in this course"
            )

        if not quiz.get("isPublished", False):
            raise HTTPException(status_code=403, detail="Quiz is not published")

    include_answers = user["role"] in ("instructor", "admin")
    return {
        "success": True,
        "data": quiz_to_public(quiz, include_answers),
        "message": "ok",
    }


# ========== UPDATE QUIZ ==========
@router.patch("/{quiz_id}")
async def update_quiz(
    quiz_id: str,
    payload: UpdateQuizRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    """Update quiz details"""
    db = database.db

    if not ObjectId.is_valid(quiz_id):
        raise HTTPException(status_code=400, detail="Invalid quiz ID")

    quiz = await db.quizzes.find_one({"_id": ObjectId(quiz_id)})
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    # Check ownership
    course = await db.courses.find_one({"_id": ObjectId(quiz["courseId"])})
    if user["role"] == "instructor":
        instructor_id = course.get("instructorId")
        if not instructor_id or instructor_id != str(user["_id"]):
            raise HTTPException(status_code=403, detail="You don't own this course")

    update_data = {}
    fields = [
        "title",
        "description",
        "timeLimit",
        "passingScore",
        "attemptsAllowed",
        "isPublished",
    ]
    for field in fields:
        value = getattr(payload, field, None)
        if value is not None:
            update_data[field] = value

    # Update questions if provided
    if payload.questions is not None:
        questions = []
        for i, q in enumerate(payload.questions):
            question_data = {
                "id": i,
                "question": q.question,
                "options": q.options,
                "type": q.type,
                "points": q.points,
            }
            if q.type == "single":
                question_data["correctAnswer"] = q.correctAnswer
            else:
                question_data["correctAnswers"] = q.correctAnswers or []
            questions.append(question_data)
        update_data["questions"] = questions
        update_data["totalQuestions"] = len(questions)

    update_data["updatedAt"] = datetime.utcnow()

    if update_data:
        await db.quizzes.update_one({"_id": ObjectId(quiz_id)}, {"$set": update_data})

    updated_quiz = await db.quizzes.find_one({"_id": ObjectId(quiz_id)})

    return {
        "success": True,
        "data": quiz_to_public(updated_quiz, include_answers=True),
        "message": "Quiz updated successfully",
    }


# ========== DELETE QUIZ ==========
@router.delete("/{quiz_id}")
async def delete_quiz(
    quiz_id: str, user: dict = Depends(require_roles("instructor", "admin"))
):
    """Delete a quiz"""
    db = database.db

    if not ObjectId.is_valid(quiz_id):
        raise HTTPException(status_code=400, detail="Invalid quiz ID")

    quiz = await db.quizzes.find_one({"_id": ObjectId(quiz_id)})
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    # Check ownership
    course = await db.courses.find_one({"_id": ObjectId(quiz["courseId"])})
    if user["role"] == "instructor":
        instructor_id = course.get("instructorId")
        if not instructor_id or instructor_id != str(user["_id"]):
            raise HTTPException(status_code=403, detail="You don't own this course")

    # Delete quiz and all attempts
    await db.quizzes.delete_one({"_id": ObjectId(quiz_id)})
    await db.quiz_attempts.delete_many({"quizId": quiz_id})

    return {"success": True, "data": None, "message": "Quiz deleted successfully"}


# ========== SUBMIT QUIZ ==========
@router.post("/{quiz_id}/submit")
async def submit_quiz(
    quiz_id: str,
    payload: SubmitQuizRequest,
    user: dict = Depends(require_roles("student")),
):
    """Submit a quiz attempt"""
    db = database.db

    if not ObjectId.is_valid(quiz_id):
        raise HTTPException(status_code=400, detail="Invalid quiz ID")

    quiz = await db.quizzes.find_one({"_id": ObjectId(quiz_id)})
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    if not quiz.get("isPublished", False):
        raise HTTPException(status_code=403, detail="Quiz is not published")

    # Check attempts
    attempts_count = await db.quiz_attempts.count_documents(
        {"quizId": quiz_id, "studentId": str(user["_id"])}
    )

    if attempts_count >= quiz.get("attemptsAllowed", 1):
        raise HTTPException(status_code=400, detail="Maximum attempts reached")

    # Check if there's an in-progress attempt
    existing_attempt = await db.quiz_attempts.find_one(
        {"quizId": quiz_id, "studentId": str(user["_id"]), "status": "in_progress"}
    )

    if existing_attempt:
        # Update existing attempt
        attempt_id = existing_attempt["_id"]
        await db.quiz_attempts.update_one(
            {"_id": attempt_id},
            {
                "$set": {
                    "answers": payload.answers,
                    "submittedAt": datetime.utcnow(),
                    "status": "submitted",
                }
            },
        )
        attempt = await db.quiz_attempts.find_one({"_id": attempt_id})
    else:
        # Create new attempt
        doc = quiz_attempt_doc(quiz_id, str(user["_id"]), payload.answers)
        doc["submittedAt"] = datetime.utcnow()
        doc["status"] = "submitted"
        result = await db.quiz_attempts.insert_one(doc)
        attempt = await db.quiz_attempts.find_one({"_id": result.inserted_id})

    # Auto-grade if possible
    questions = quiz.get("questions", [])
    if questions:
        score = 0
        total_points = sum(q.get("points", 1) for q in questions)

        for answer in payload.answers:
            q_id = answer.get("questionId")
            selected = answer.get("selected")

            if q_id is not None and q_id < len(questions):
                q = questions[q_id]
                if q.get("type") == "single":
                    if selected == q.get("correctAnswer"):
                        score += q.get("points", 1)
                else:  # multiple
                    correct = set(q.get("correctAnswers", []))
                    selected_set = (
                        set(selected) if isinstance(selected, list) else set()
                    )
                    if correct and selected_set == correct:
                        score += q.get("points", 1)

        percentage = (score / total_points) * 100 if total_points > 0 else 0
        passed = percentage >= quiz.get("passingScore", 60)

        await db.quiz_attempts.update_one(
            {"_id": attempt["_id"]},
            {
                "$set": {
                    "score": score,
                    "percentage": round(percentage, 2),
                    "passed": passed,
                    "gradedBy": "auto",
                    "gradedAt": datetime.utcnow(),
                    "status": "graded",
                }
            },
        )
        attempt = await db.quiz_attempts.find_one({"_id": attempt["_id"]})

    return {
        "success": True,
        "data": quiz_attempt_to_public(attempt),
        "message": "Quiz submitted successfully",
    }


# ========== LIST SUBMITTED QUIZZES ==========
@router.get("/{quiz_id}/attempts")
async def list_quiz_attempts(
    quiz_id: str, user: dict = Depends(require_roles("instructor", "admin"))
):
    """List all attempts for a quiz"""
    db = database.db

    if not ObjectId.is_valid(quiz_id):
        raise HTTPException(status_code=400, detail="Invalid quiz ID")

    quiz = await db.quizzes.find_one({"_id": ObjectId(quiz_id)})
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    # Check ownership
    course = await db.courses.find_one({"_id": ObjectId(quiz["courseId"])})
    if user["role"] == "instructor":
        instructor_id = course.get("instructorId")
        if not instructor_id or instructor_id != str(user["_id"]):
            raise HTTPException(status_code=403, detail="You don't own this course")

    cursor = db.quiz_attempts.find({"quizId": quiz_id}).sort("submittedAt", -1)
    attempts = [quiz_attempt_to_public(a) async for a in cursor]

    return {"success": True, "data": attempts, "message": "ok"}


# ========== GRADE SUBMITTED QUIZ ==========
@router.patch("/attempts/{attempt_id}/grade")
async def grade_quiz_attempt(
    attempt_id: str,
    payload: GradeQuizRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    """Manually grade a quiz attempt"""
    db = database.db

    if not ObjectId.is_valid(attempt_id):
        raise HTTPException(status_code=400, detail="Invalid attempt ID")

    attempt = await db.quiz_attempts.find_one({"_id": ObjectId(attempt_id)})
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")

    # Get quiz to check ownership
    quiz = await db.quizzes.find_one({"_id": ObjectId(attempt["quizId"])})
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    course = await db.courses.find_one({"_id": ObjectId(quiz["courseId"])})
    if user["role"] == "instructor":
        instructor_id = course.get("instructorId")
        if not instructor_id or instructor_id != str(user["_id"]):
            raise HTTPException(status_code=403, detail="You don't own this course")

    # Calculate percentage
    total_points = sum(q.get("points", 1) for q in quiz.get("questions", []))
    percentage = (payload.score / total_points) * 100 if total_points > 0 else 0
    passed = percentage >= quiz.get("passingScore", 60)

    await db.quiz_attempts.update_one(
        {"_id": ObjectId(attempt_id)},
        {
            "$set": {
                "score": payload.score,
                "percentage": round(percentage, 2),
                "passed": passed,
                "feedback": payload.feedback,
                "gradedBy": str(user["_id"]),
                "gradedAt": datetime.utcnow(),
                "status": "graded",
            }
        },
    )

    updated_attempt = await db.quiz_attempts.find_one({"_id": ObjectId(attempt_id)})

    return {
        "success": True,
        "data": quiz_attempt_to_public(updated_attempt),
        "message": "Quiz graded successfully",
    }


# ========== MY SUBMITTED QUIZZES ==========
@router.get("/my/attempts")
async def get_my_quiz_attempts(
    course_id: Optional[str] = Query(None),
    user: dict = Depends(require_roles("student")),
):
    """Get all quiz attempts for the current student"""
    db = database.db

    query = {"studentId": str(user["_id"])}

    # If course_id provided, filter by course
    if course_id:
        # Get all quiz IDs for this course
        quizzes = await db.quizzes.find({"courseId": course_id}).to_list(length=None)
        quiz_ids = [str(q["_id"]) for q in quizzes]
        query["quizId"] = {"$in": quiz_ids}

    cursor = db.quiz_attempts.find(query).sort("submittedAt", -1)
    attempts = [quiz_attempt_to_public(a) async for a in cursor]

    # Add quiz titles
    for attempt in attempts:
        quiz = await db.quizzes.find_one({"_id": ObjectId(attempt["quizId"])})
        if quiz:
            attempt["quizTitle"] = quiz.get("title", "Unknown Quiz")
            attempt["courseId"] = quiz.get("courseId")

    return {"success": True, "data": attempts, "message": "ok"}
