"""Student grading report for instructor + student grades pages."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import require_roles
from app.core.database import database

router = APIRouter(prefix="/grading", tags=["grading"])

DEFAULT_WEIGHTS = {
    "Assignment": 25.0,
    "Quiz": 25.0,
    "Project": 25.0,
    "Exam": 25.0,
}


class WeightsPayload(BaseModel):
    Assignment: float = Field(default=25, ge=0, le=100)
    Quiz: float = Field(default=25, ge=0, le=100)
    Project: float = Field(default=25, ge=0, le=100)
    Exam: float = Field(default=25, ge=0, le=100)


def _to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


async def _instructor_can_access_course(
    db, user: dict, course: dict, course_id: str
) -> bool:
    if user["role"] == "admin":
        return True
    uid = str(user["_id"])
    if str(course.get("instructorId") or "") == uid:
        return True
    for coll in ("assignments", "quizzes", "exams", "projects"):
        found = await db[coll].find_one(
            {"courseId": course_id, "instructorId": uid}
        )
        if found:
            return True
    return False


async def _grade_rows_for(
    db,
    *,
    collection: str,
    course_id: str,
    student_id: str,
    sub_coll: str,
    id_field: str,
    for_student: bool = False,
) -> list[dict]:
    rows = []
    cursor = db[collection].find(
        {
            "courseId": course_id,
            "$or": [
                {"status": "published"},
                {"isPublished": True},
                {"status": {"$exists": False}},
            ],
        }
    ).sort("createdAt", -1)

    async for item in cursor:
        item_id = str(item["_id"])
        total = float(
            item.get("totalMarks")
            or item.get("maxScore")
            or item.get("max_score")
            or 100
        )

        subs = await (
            db[sub_coll]
            .find({id_field: item_id, "studentId": student_id})
            .sort([("attemptNumber", -1), ("submittedAt", -1)])
            .limit(1)
            .to_list(length=1)
        )
        sub = subs[0] if subs else None

        marks_hidden = bool(sub.get("marksHidden", False)) if sub else False

        if not sub:
            status = "not_submitted"
            obtained = None
            remarks = ""
        else:
            obtained = (
                sub.get("marksAwarded")
                if sub.get("marksAwarded") is not None
                else sub.get("score")
            )
            remarks = sub.get("feedback") or ""

            if for_student and marks_hidden and (
                sub.get("status") == "graded" or obtained is not None
            ):
                status = "not_graded_yet"
                obtained = None
                remarks = ""
            elif sub.get("status") == "graded" or obtained is not None:
                status = "submitted"
            elif sub.get("status") in ("submitted", "late"):
                status = "pending"
            else:
                status = "pending"

        # ✅ Item's actual content, used by AI Insights to generate real
        # "what to study next" topics instead of guessing from course name.
        raw_description = (item.get("description") or "").strip()
        if collection == "quizzes":
            question_texts = [
                q.get("question", "").strip()
                for q in (item.get("questions") or [])
                if q.get("question")
            ]
            questions_summary = "; ".join(question_texts[:5])
            description = raw_description or questions_summary or None
        else:
            description = raw_description or None

        rows.append(
            {
                "id": item_id,
                "name": item.get("title", ""),
                "totalMarks": total,
                "obtainedMarks": obtained,
                "remarks": remarks,
                "status": status,
                "submissionId": str(sub["_id"]) if sub else None,
                "marksHidden": marks_hidden,
                # ✅ NEW
                "description": description,
            }
        )
    return rows


@router.get("/student/{email}")
async def student_grading_report(
    email: str,
    courseId: str = Query(...),
    user: dict = Depends(require_roles("instructor", "admin", "student")),
):
    db = database.db
    target_email = email.lower().strip()

    student = await db.users.find_one({"email": target_email})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    if user["role"] == "student":
        if str(user.get("email", "")).lower() != target_email:
            raise HTTPException(
                status_code=403, detail="You can only view your own grades"
            )

    if not ObjectId.is_valid(courseId):
        raise HTTPException(status_code=400, detail="Invalid courseId")

    course = await db.courses.find_one({"_id": ObjectId(courseId)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    if user["role"] in ("instructor", "admin"):
        if not await _instructor_can_access_course(db, user, course, courseId):
            raise HTTPException(status_code=403, detail="Not your course")

    if user["role"] == "student":
        enrolled = await db.enrollments.find_one(
            {"courseId": courseId, "userId": str(user["_id"])}
        )
        if not enrolled:
            raise HTTPException(
                status_code=403, detail="Not enrolled in this course"
            )

    sid = str(student["_id"])
    instructor_name = ""
    if course.get("instructorId") and ObjectId.is_valid(
        str(course["instructorId"])
    ):
        ins = await db.users.find_one(
            {"_id": ObjectId(course["instructorId"])}
        )
        if ins:
            instructor_name = ins.get("name", "")

    for_student = user["role"] == "student"

    assignments = await _grade_rows_for(
        db,
        collection="assignments",
        course_id=courseId,
        student_id=sid,
        sub_coll="submissions",
        id_field="assignmentId",
        for_student=for_student,
    )
    quizzes = await _grade_rows_for(
        db,
        collection="quizzes",
        course_id=courseId,
        student_id=sid,
        sub_coll="quiz_attempts",
        id_field="quizId",
        for_student=for_student,
    )
    projects = await _grade_rows_for(
        db,
        collection="projects",
        course_id=courseId,
        student_id=sid,
        sub_coll="submissions",
        id_field="assignmentId",
        for_student=for_student,
    )
    exams = await _grade_rows_for(
        db,
        collection="exams",
        course_id=courseId,
        student_id=sid,
        sub_coll="exam_submissions",
        id_field="examId",
        for_student=for_student,
    )

    stored = course.get("gradingWeights") or {}
    w_assignment = float(stored.get("Assignment", DEFAULT_WEIGHTS["Assignment"]))
    w_quiz = float(stored.get("Quiz", DEFAULT_WEIGHTS["Quiz"]))
    w_project = float(stored.get("Project", DEFAULT_WEIGHTS["Project"]))
    w_exam = float(stored.get("Exam", DEFAULT_WEIGHTS["Exam"]))

    components = []
    buckets = [
        ("Assignment", assignments, w_assignment),
        ("Quiz", quizzes, w_quiz),
        ("Project", projects, w_project),
        ("Exam", exams, w_exam),
    ]
    for name, rows, weight in buckets:
        total_marks = sum(r["totalMarks"] for r in rows) if rows else 0
        obtained = sum(
            (r["obtainedMarks"] or 0)
            for r in rows
            if r["obtainedMarks"] is not None
        )
        weighted = (obtained / total_marks) * weight if total_marks > 0 else 0.0
        components.append(
            {
                "component": name,
                "weightagePercent": weight,
                "totalMarks": total_marks,
                "obtainedMarks": obtained,
                "weightedScorePercent": round(weighted, 2),
            }
        )

    total_weight = w_assignment + w_quiz + w_project + w_exam
    total_marks = sum(c["totalMarks"] for c in components)
    total_obtained = sum(c["obtainedMarks"] for c in components)
    overall = sum(c["weightedScorePercent"] for c in components)

    return {
        "success": True,
        "data": {
            "courseId": courseId,
            "courseTitle": course.get("title", ""),
            "instructorName": instructor_name,
            "assignments": assignments,
            "quizzes": quizzes,
            "projects": projects,
            "exams": exams,
            "performance": components,
            "totalWeightagePercent": total_weight,
            "totalMarks": total_marks,
            "totalObtainedMarks": total_obtained,
            "overallWeightedScorePercent": round(overall, 2),
        },
        "message": "ok",
    }


@router.get("/weights/{course_id}")
async def get_course_weights(
    course_id: str,
    user: dict = Depends(require_roles("instructor", "admin", "student")),
):
    db = database.db
    if not ObjectId.is_valid(course_id):
        raise HTTPException(status_code=400, detail="Invalid courseId")

    course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    if user["role"] == "student":
        enrolled = await db.enrollments.find_one(
            {"courseId": course_id, "userId": str(user["_id"])}
        )
        if not enrolled:
            raise HTTPException(status_code=403, detail="Not enrolled")
    elif user["role"] == "instructor":
        if not await _instructor_can_access_course(db, user, course, course_id):
            raise HTTPException(status_code=403, detail="Not your course")

    stored = course.get("gradingWeights") or {}
    weights = {
        "Assignment": float(
            stored.get("Assignment", DEFAULT_WEIGHTS["Assignment"])
        ),
        "Quiz": float(stored.get("Quiz", DEFAULT_WEIGHTS["Quiz"])),
        "Project": float(stored.get("Project", DEFAULT_WEIGHTS["Project"])),
        "Exam": float(stored.get("Exam", DEFAULT_WEIGHTS["Exam"])),
    }
    return {"success": True, "data": weights, "message": "ok"}


@router.put("/weights/{course_id}")
async def set_course_weights(
    course_id: str,
    payload: WeightsPayload,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    """Instructor saves KPI weightages — students see them automatically."""
    db = database.db
    if not ObjectId.is_valid(course_id):
        raise HTTPException(status_code=400, detail="Invalid courseId")

    course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    if user["role"] == "instructor":
        if not await _instructor_can_access_course(db, user, course, course_id):
            raise HTTPException(status_code=403, detail="Not your course")

    weights = {
        "Assignment": float(payload.Assignment),
        "Quiz": float(payload.Quiz),
        "Project": float(payload.Project),
        "Exam": float(payload.Exam),
    }
    total = sum(weights.values())
    if abs(total - 100.0) > 0.5:
        raise HTTPException(
            status_code=400,
            detail=f"Weightages must total 100% (got {total})",
        )

    await db.courses.update_one(
        {"_id": ObjectId(course_id)},
        {
            "$set": {
                "gradingWeights": weights,
                "updatedAt": datetime.utcnow(),
            }
        },
    )

    updated = await db.courses.find_one({"_id": ObjectId(course_id)})
    saved = (updated or {}).get("gradingWeights") or weights

    return {
        "success": True,
        "data": {
            "Assignment": float(saved.get("Assignment", 25)),
            "Quiz": float(saved.get("Quiz", 25)),
            "Project": float(saved.get("Project", 25)),
            "Exam": float(saved.get("Exam", 25)),
        },
        "message": "weights saved",
    }

# """Student grading report for instructor + student grades pages."""

# from __future__ import annotations

# from datetime import datetime
# from typing import Any, Optional

# from bson import ObjectId
# from fastapi import APIRouter, Depends, HTTPException, Query

# from app.api.deps import require_roles
# from app.core.database import database

# router = APIRouter(prefix="/grading", tags=["grading"])


# def _to_iso(value: Any) -> Optional[str]:
#     if value is None:
#         return None
#     if isinstance(value, datetime):
#         return value.isoformat() + ("Z" if value.tzinfo is None else "")
#     return str(value)


# async def _instructor_can_access_course(
#     db, user: dict, course: dict, course_id: str
# ) -> bool:
#     if user["role"] == "admin":
#         return True
#     uid = str(user["_id"])
#     if str(course.get("instructorId") or "") == uid:
#         return True
#     for coll in ("assignments", "quizzes", "exams", "projects"):
#         found = await db[coll].find_one(
#             {"courseId": course_id, "instructorId": uid}
#         )
#         if found:
#             return True
#     return False


# async def _grade_rows_for(
#     db,
#     *,
#     collection: str,
#     course_id: str,
#     student_id: str,
#     sub_coll: str,
#     id_field: str,
#     for_student: bool = False,
# ) -> list[dict]:
#     rows = []
#     cursor = db[collection].find(
#         {
#             "courseId": course_id,
#             "$or": [
#                 {"status": "published"},
#                 {"isPublished": True},
#                 {"status": {"$exists": False}},
#             ],
#         }
#     ).sort("createdAt", -1)

#     async for item in cursor:
#         item_id = str(item["_id"])
#         total = float(
#             item.get("totalMarks")
#             or item.get("maxScore")
#             or item.get("max_score")
#             or 100
#         )

#         sub_cursor = (
#             db[sub_coll]
#             .find({id_field: item_id, "studentId": student_id})
#             .sort([("attemptNumber", -1), ("submittedAt", -1)])
#             .limit(1)
#         )
#         subs = await sub_cursor.to_list(length=1)
#         sub = subs[0] if subs else None

#         marks_hidden = bool(sub.get("marksHidden", False)) if sub else False

#         if not sub:
#             status = "not_submitted"
#             obtained = None
#             remarks = ""
#         else:
#             obtained = (
#                 sub.get("marksAwarded")
#                 if sub.get("marksAwarded") is not None
#                 else sub.get("score")
#             )
#             remarks = sub.get("feedback") or ""

#             # Student view: hide marks/feedback until instructor releases them
#             if for_student and marks_hidden and (
#                 sub.get("status") == "graded" or obtained is not None
#             ):
#                 status = "not_graded_yet"
#                 obtained = None
#                 remarks = ""
#             elif sub.get("status") == "graded" or obtained is not None:
#                 status = "submitted"
#             elif sub.get("status") in ("submitted", "late"):
#                 status = "pending"
#             else:
#                 status = "pending"

#         # ✅ NEW: build a real content string for this item so AI Insights can
#         # ground "what to study next" topics in what this specific
#         # assignment/quiz/exam/project actually covered, instead of guessing
#         # from the course name. Falls back gracefully if description missing.
#         raw_description = (item.get("description") or "").strip()
#         if collection == "quizzes":
#             # For quizzes, also surface the question text (first few) so the
#             # AI has real topic signal even if the quiz has no description.
#             question_texts = [
#                 q.get("question", "").strip()
#                 for q in (item.get("questions") or [])
#                 if q.get("question")
#             ]
#             questions_summary = "; ".join(question_texts[:5])
#             description = raw_description or questions_summary or None
#         else:
#             description = raw_description or None

#         rows.append(
#             {
#                 "id": item_id,
#                 "name": item.get("title", ""),
#                 "totalMarks": total,
#                 "obtainedMarks": obtained,
#                 "remarks": remarks,
#                 "status": status,
#                 "submissionId": str(sub["_id"]) if sub else None,
#                 "marksHidden": marks_hidden,
#                 # ✅ NEW
#                 "description": description,
#             }
#         )
#     return rows


# @router.get("/student/{email}")
# async def student_grading_report(
#     email: str,
#     courseId: str = Query(...),
#     user: dict = Depends(require_roles("instructor", "admin", "student")),
# ):
#     db = database.db
#     target_email = email.lower().strip()

#     student = await db.users.find_one({"email": target_email})
#     if not student:
#         raise HTTPException(status_code=404, detail="Student not found")

#     # Student can only view their own grades
#     if user["role"] == "student":
#         if str(user.get("email", "")).lower() != target_email:
#             raise HTTPException(
#                 status_code=403, detail="You can only view your own grades"
#             )

#     if not ObjectId.is_valid(courseId):
#         raise HTTPException(status_code=400, detail="Invalid courseId")

#     course = await db.courses.find_one({"_id": ObjectId(courseId)})
#     if not course:
#         raise HTTPException(status_code=404, detail="Course not found")

#     # Instructor/admin ownership check (students skip this)
#     if user["role"] in ("instructor", "admin"):
#         if not await _instructor_can_access_course(db, user, course, courseId):
#             raise HTTPException(status_code=403, detail="Not your course")

#     # Student must be enrolled in the course
#     if user["role"] == "student":
#         enrolled = await db.enrollments.find_one(
#             {"courseId": courseId, "userId": str(user["_id"])}
#         )
#         if not enrolled:
#             raise HTTPException(
#                 status_code=403, detail="Not enrolled in this course"
#             )

#     sid = str(student["_id"])
#     instructor_name = ""
#     if course.get("instructorId") and ObjectId.is_valid(
#         str(course["instructorId"])
#     ):
#         ins = await db.users.find_one(
#             {"_id": ObjectId(course["instructorId"])}
#         )
#         if ins:
#             instructor_name = ins.get("name", "")

#     for_student = user["role"] == "student"

#     assignments = await _grade_rows_for(
#         db,
#         collection="assignments",
#         course_id=courseId,
#         student_id=sid,
#         sub_coll="submissions",
#         id_field="assignmentId",
#         for_student=for_student,
#     )
#     quizzes = await _grade_rows_for(
#         db,
#         collection="quizzes",
#         course_id=courseId,
#         student_id=sid,
#         sub_coll="quiz_attempts",
#         id_field="quizId",
#         for_student=for_student,
#     )
#     projects = await _grade_rows_for(
#         db,
#         collection="projects",
#         course_id=courseId,
#         student_id=sid,
#         sub_coll="submissions",
#         id_field="assignmentId",
#         for_student=for_student,
#     )
#     exams = await _grade_rows_for(
#         db,
#         collection="exams",
#         course_id=courseId,
#         student_id=sid,
#         sub_coll="exam_submissions",
#         id_field="examId",
#         for_student=for_student,
#     )

#     # ---------- Attendance calculation ----------
#     attendance_cursor = db.attendance.find({
#         "courseId": courseId,
#         "studentId": sid,
#     })
#     attendance_records = [doc async for doc in attendance_cursor]

#     total_sessions = len(attendance_records)
#     present_count = sum(
#         1 for r in attendance_records
#         if str(r.get("status", "")).lower() in ("present", "late")
#     )

#     if total_sessions > 0:
#         attendance_percent = (present_count / total_sessions) * 100
#     else:
#         attendance_percent = 0.0

#     # ---------- Performance components (new weightages) ----------
#     components = []

#     buckets = [
#         ("Assignment", assignments, 25),
#         ("Quiz", quizzes, 15),
#         ("Project", projects, 30),
#         ("Exam", exams, 20),
#     ]

#     for name, rows, weight in buckets:
#         total_marks = sum(r["totalMarks"] for r in rows) if rows else 0
#         obtained = sum(
#             (r["obtainedMarks"] or 0)
#             for r in rows
#             if r["obtainedMarks"] is not None
#         ) if rows else 0

#         if total_marks > 0:
#             weighted = (obtained / total_marks) * weight
#         else:
#             weighted = 0.0

#         components.append({
#             "component": name,
#             "weightagePercent": weight,
#             "totalMarks": total_marks,
#             "obtainedMarks": obtained,
#             "weightedScorePercent": round(weighted, 2),
#         })

#     # Attendance (always included)
#     attendance_weight = 10
#     attendance_weighted = (attendance_percent / 100) * attendance_weight

#     components.append({
#         "component": "Attendance",
#         "weightagePercent": attendance_weight,
#         "totalMarks": 100,
#         "obtainedMarks": round(attendance_percent, 2),
#         "weightedScorePercent": round(attendance_weighted, 2),
#     })

#     # Totals
#     total_weight = sum(c["weightagePercent"] for c in components)
#     total_marks = sum(c["totalMarks"] for c in components)
#     total_obtained = sum(c["obtainedMarks"] for c in components)
#     overall = sum(c["weightedScorePercent"] for c in components)

#     return {
#         "success": True,
#         "data": {
#             "courseId": courseId,
#             "courseTitle": course.get("title", ""),
#             "instructorName": instructor_name,
#             "assignments": assignments,
#             "quizzes": quizzes,
#             "projects": projects,
#             "exams": exams,
#             "performance": components,
#             "totalWeightagePercent": total_weight,
#             "totalMarks": total_marks,
#             "totalObtainedMarks": total_obtained,
#             "overallWeightedScorePercent": round(overall, 2),
#         },
#         "message": "ok",
#     }