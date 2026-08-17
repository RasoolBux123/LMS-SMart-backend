from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from datetime import datetime
from app.core.database import database
from app.api.deps import require_roles, get_current_user
from app.models.course import course_to_public

router = APIRouter(prefix="/enrollments", tags=["enrollments"])


@router.post("/course/{course_id}")
async def enroll_in_course(
    course_id: str, user: dict = Depends(require_roles("student"))
):
    db = database.db
    if not ObjectId.is_valid(course_id):
        raise HTTPException(status_code=400, detail="Invalid course ID")
    course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course["status"] != "active":
        raise HTTPException(status_code=400, detail="Course is not active")
    existing = await db.enrollments.find_one(
        {"courseId": course_id, "userId": str(user["_id"])}
    )
    if existing:
        raise HTTPException(status_code=400, detail="Already enrolled")
    doc = {
        "courseId": course_id,
        "userId": str(user["_id"]),
        "enrolledAt": datetime.utcnow(),
        "status": "active",
        "progress": 0,
        "lastActivityAt": datetime.utcnow(),
    }
    result = await db.enrollments.insert_one(doc)
    await db.courses.update_one(
        {"_id": ObjectId(course_id)}, {"$inc": {"enrollmentCount": 1}}
    )
    return {
        "success": True,
        "data": {"id": str(result.inserted_id)},
        "message": "Successfully enrolled",
    }


@router.delete("/course/{course_id}")
async def unenroll_from_course(
    course_id: str, user: dict = Depends(require_roles("student"))
):
    db = database.db
    result = await db.enrollments.delete_one(
        {"courseId": course_id, "userId": str(user["_id"])}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Not enrolled")
    await db.courses.update_one(
        {"_id": ObjectId(course_id)}, {"$inc": {"enrollmentCount": -1}}
    )
    return {"success": True, "data": None, "message": "Unenrolled successfully"}


@router.get("/my-courses")
async def get_my_enrolled_courses(user: dict = Depends(get_current_user)):
    db = database.db
    enrollments = await db.enrollments.find({"userId": str(user["_id"])}).to_list(
        length=None
    )
    course_ids = [ObjectId(e["courseId"]) for e in enrollments]
    if not course_ids:
        return {"success": True, "data": [], "message": "No courses enrolled"}
    cursor = db.courses.find({"_id": {"$in": course_ids}, "status": "active"})
    courses = []
    async for course in cursor:
        course_data = course_to_public(course)
        for enrollment in enrollments:
            if enrollment["courseId"] == course_data["id"]:
                course_data["progress"] = enrollment.get("progress", 0)
                course_data["enrolledAt"] = enrollment["enrolledAt"]
                break
        courses.append(course_data)
    return {"success": True, "data": courses, "message": "ok"}


@router.get("/course/{course_id}/students")
async def get_course_students(
    course_id: str, user: dict = Depends(require_roles("instructor", "admin"))
):
    db = database.db
    course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if user["role"] == "instructor" and course["instructorId"] != str(user["_id"]):
        raise HTTPException(status_code=403, detail="You don't own this course")
    enrollments = await db.enrollments.find(
        {"courseId": course_id, "status": "active"}
    ).to_list(length=None)
    students = []
    for enrollment in enrollments:
        student = await db.users.find_one({"_id": ObjectId(enrollment["userId"])})
        if student:
            students.append(
                {
                    "id": str(student["_id"]),
                    "name": student["name"],
                    "email": student["email"],
                    "progress": enrollment.get("progress", 0),
                    "enrolledAt": enrollment["enrolledAt"],
                    "lastActivityAt": enrollment.get("lastActivityAt"),
                }
            )
    return {"success": True, "data": students, "message": "ok"}
