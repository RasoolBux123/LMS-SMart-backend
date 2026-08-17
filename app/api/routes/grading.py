"""Student grading report for instructor + student grades pages."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_roles
from app.core.database import database

router = APIRouter(prefix="/grading", tags=["grading"])


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

        sub_cursor = (
            db[sub_coll]
            .find({id_field: item_id, "studentId": student_id})
            .sort([("attemptNumber", -1), ("submittedAt", -1)])
            .limit(1)
        )
        subs = await sub_cursor.to_list(length=1)
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

            # Student view: hide marks/feedback until instructor releases them
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

    # Student can only view their own grades
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

    # Instructor/admin ownership check (students skip this)
    if user["role"] in ("instructor", "admin"):
        if not await _instructor_can_access_course(db, user, course, courseId):
            raise HTTPException(status_code=403, detail="Not your course")

    # Student must be enrolled in the course
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

    components = []
    buckets = [
        ("Assignment", assignments, 25),
        ("Quiz", quizzes, 25),
        ("Project", projects, 25),
        ("Exam", exams, 25),
    ]
    for name, rows, weight in buckets:
        if not rows:
            continue
        total_marks = sum(r["totalMarks"] for r in rows) or 1
        obtained = sum(
            (r["obtainedMarks"] or 0)
            for r in rows
            if r["obtainedMarks"] is not None
        )
        weighted = (obtained / total_marks) * weight if total_marks else 0
        components.append(
            {
                "component": name,
                "weightagePercent": weight,
                "totalMarks": total_marks,
                "obtainedMarks": obtained,
                "weightedScorePercent": round(weighted, 2),
            }
        )

    total_weight = sum(c["weightagePercent"] for c in components) or 100
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
#             if sub.get("status") == "graded" or obtained is not None:
#                 status = "submitted"
#             elif sub.get("status") in ("submitted", "late"):
#                 status = "pending"
#             else:
#                 status = "pending"
#             remarks = sub.get("feedback") or ""

#         rows.append(
#             {
#                 "id": item_id,
#                 "name": item.get("title", ""),
#                 "totalMarks": total,
#                 "obtainedMarks": obtained,
#                 "remarks": remarks,
#                 "status": status,
#                 "submissionId": str(sub["_id"]) if sub else None,
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

#     assignments = await _grade_rows_for(
#         db,
#         collection="assignments",
#         course_id=courseId,
#         student_id=sid,
#         sub_coll="submissions",
#         id_field="assignmentId",
#     )
#     quizzes = await _grade_rows_for(
#         db,
#         collection="quizzes",
#         course_id=courseId,
#         student_id=sid,
#         sub_coll="quiz_attempts",
#         id_field="quizId",
#     )
#     projects = await _grade_rows_for(
#         db,
#         collection="projects",
#         course_id=courseId,
#         student_id=sid,
#         sub_coll="submissions",
#         id_field="assignmentId",
#     )
#     exams = await _grade_rows_for(
#         db,
#         collection="exams",
#         course_id=courseId,
#         student_id=sid,
#         sub_coll="exam_submissions",
#         id_field="examId",
#     )

#     components = []
#     buckets = [
#         ("Assignment", assignments, 25),
#         ("Quiz", quizzes, 25),
#         ("Project", projects, 25),
#         ("Exam", exams, 25),
#     ]
#     for name, rows, weight in buckets:
#         if not rows:
#             continue
#         total_marks = sum(r["totalMarks"] for r in rows) or 1
#         obtained = sum(
#             (r["obtainedMarks"] or 0)
#             for r in rows
#             if r["obtainedMarks"] is not None
#         )
#         weighted = (obtained / total_marks) * weight if total_marks else 0
#         components.append(
#             {
#                 "component": name,
#                 "weightagePercent": weight,
#                 "totalMarks": total_marks,
#                 "obtainedMarks": obtained,
#                 "weightedScorePercent": round(weighted, 2),
#             }
#         )

#     total_weight = sum(c["weightagePercent"] for c in components) or 100
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




