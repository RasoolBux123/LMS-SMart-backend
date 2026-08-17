from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from bson.errors import InvalidId

from app.core.database import database
from app.api.deps import get_current_user, require_roles
from app.models.attendance import (
    new_attendance_doc,
    attendance_to_public,
)
from app.schemas.attendance import MarkAttendanceRequest



router = APIRouter(
    prefix="/attendance",
    tags=["attendance"]
)


# ---- Instructor/Admin: mark attendance ----
@router.post("")
async def mark_attendance(
    payload: MarkAttendanceRequest,
    user: dict = Depends(require_roles("instructor", "admin"))
):
    db = database.db

    # Validate course ID
    try:
        course_id = ObjectId(payload.courseId)
    except InvalidId:
        raise HTTPException(
            status_code=400,
            detail="Invalid course ID"
        )

    # Check course
    course = await db.courses.find_one({
        "_id": course_id
    })

    if not course:
        raise HTTPException(
            status_code=404,
            detail="Course not found"
        )

    # Save attendance for each student
    for item in payload.attendance:

        try:
            student_id = ObjectId(item.studentId)
        except InvalidId:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid student ID: {item.studentId}"
            )

        # Check student
        student = await db.users.find_one({
            "_id": student_id,
            "role": "student"
        })

        if not student:
            raise HTTPException(
                status_code=404,
                detail=f"Student not found: {item.studentId}"
            )

        # Check if attendance already exists
        existing = await db.attendance.find_one({
            "courseId": payload.courseId,
            "studentId": item.studentId,
            "date": payload.date,
        })

        if existing:
            # Update existing attendance
            await db.attendance.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "status": item.status,
                        "markedBy": str(user["_id"]),
                    }
                }
            )
        else:
            # Create attendance
            doc = new_attendance_doc(
                course_id=payload.courseId,
                student_id=item.studentId,
                date=payload.date,
                status=item.status,
                marked_by=str(user["_id"]),
            )

            await db.attendance.insert_one(doc)

    return {
        "success": True,
        "message": "attendance marked"
    }


# ---- Student: view own attendance ----
@router.get("/my")
async def my_attendance(
    user: dict = Depends(require_roles("student"))
):
    db = database.db

    cursor = db.attendance.find({
        "studentId": str(user["_id"])
    }).sort("date", -1)

    attendance = [
        attendance_to_public(item)
        async for item in cursor
    ]

    return {
        "success": True,
        "data": attendance,
        "message": "ok"
    }


# ---- Instructor/Admin: view all attendance for a course ----
@router.get("/course/{course_id}")
async def course_attendance(
    course_id: str,
    user: dict = Depends(require_roles("instructor", "admin"))
):
    db = database.db

    try:
        ObjectId(course_id)
    except InvalidId:
        raise HTTPException(
            status_code=400,
            detail="Invalid course ID"
        )

    cursor = db.attendance.find({
        "courseId": course_id
    }).sort("date", -1)

    attendance = [
        attendance_to_public(item)
        async for item in cursor
    ]

    return {
        "success": True,
        "data": attendance,
        "message": "ok"
    }


# ---- Instructor/Admin: view attendance for a student ----
@router.get("/student/{student_id}")
async def student_attendance(
    student_id: str,
    user: dict = Depends(require_roles("instructor", "admin"))
):
    db = database.db

    try:
        ObjectId(student_id)
    except InvalidId:
        raise HTTPException(
            status_code=400,
            detail="Invalid student ID"
        )

    cursor = db.attendance.find({
        "studentId": student_id
    }).sort("date", -1)

    attendance = [
        attendance_to_public(item)
        async for item in cursor
    ]

    return {
        "success": True,
        "data": attendance,
        "message": "ok"
    }