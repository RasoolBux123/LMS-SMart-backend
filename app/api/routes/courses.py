from fastapi import APIRouter, Depends, HTTPException, Query
from bson import ObjectId
from datetime import datetime
import math
from app.core.database import database
from app.api.deps import require_roles, get_current_user
from app.models.course import new_course_doc, course_to_public
from app.schemas.course import CreateCourseRequest, UpdateCourseRequest

router = APIRouter(prefix="/courses", tags=["courses"])


@router.get("")
async def list_courses(
    search: str | None = Query(None),
    category: str | None = Query(None),
    level: str | None = Query(None),
    instructor_id: str | None = Query(None, alias="instructorId"),
    status: str | None = Query("active"),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """List courses with filters and pagination"""
    db = database.db

    # Build query
    query = {}

    if search:
        query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"description": {"$regex": search, "$options": "i"}},
        ]

    if category:
        query["category"] = category

    if level:
        query["level"] = level

    if instructor_id:
        query["instructorId"] = instructor_id

    if status:
        query["status"] = status

    # If student, only show enrolled courses
    if user["role"] == "student":
        enrollments = await db.enrollments.find({"userId": str(user["_id"])}).to_list(
            length=None
        )
        course_ids = [ObjectId(e["courseId"]) for e in enrollments]
        query["_id"] = {"$in": course_ids} if course_ids else {"$eq": None}

    # Calculate pagination
    skip = (page - 1) * limit
    total = await db.courses.count_documents(query)

    cursor = db.courses.find(query).sort("createdAt", -1).skip(skip).limit(limit)
    courses = [course_to_public(c) async for c in cursor]

    return {
        "success": True,
        "data": {
            "courses": courses,
            "pagination": {
                "page": page,
                "limit": limit,
                "total": total,
                "pages": math.ceil(total / limit) if total > 0 else 0,
            },
        },
        "message": "ok",
    }


@router.post("")
async def create_course(
    payload: CreateCourseRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    """Create a new course"""
    db = database.db

    doc = new_course_doc(
        title=payload.title,
        description=payload.description,
        instructor_id=str(user["_id"]),
        category=payload.category,
        level=payload.level,
        duration_weeks=payload.durationWeeks,
        thumbnail=payload.thumbnail,
        objectives=payload.objectives,
        prerequisites=payload.prerequisites,
    )

    result = await db.courses.insert_one(doc)
    doc["_id"] = result.inserted_id

    return {
        "success": True,
        "data": course_to_public(doc),
        "message": "Course created successfully",
    }


@router.get("/{course_id}")
async def get_course(course_id: str, user: dict = Depends(get_current_user)):
    """Get course details"""
    db = database.db

    if not ObjectId.is_valid(course_id):
        raise HTTPException(status_code=400, detail="Invalid course ID")

    course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    # Check access
    if user["role"] == "student":
        enrollment = await db.enrollments.find_one(
            {"courseId": course_id, "userId": str(user["_id"])}
        )
        if not enrollment:
            raise HTTPException(
                status_code=403, detail="You are not enrolled in this course"
            )

    return {"success": True, "data": course_to_public(course), "message": "ok"}


@router.patch("/{course_id}")
async def update_course(
    course_id: str,
    payload: UpdateCourseRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    """Update course details"""
    db = database.db

    if not ObjectId.is_valid(course_id):
        raise HTTPException(status_code=400, detail="Invalid course ID")

    course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    # Check ownership
    if user["role"] == "instructor" and course["instructorId"] != str(user["_id"]):
        raise HTTPException(status_code=403, detail="You don't own this course")

    # Prepare update data
    update_data = {
        k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None
    }
    update_data["updatedAt"] = datetime.utcnow()

    if update_data:
        await db.courses.update_one({"_id": ObjectId(course_id)}, {"$set": update_data})

    updated_course = await db.courses.find_one({"_id": ObjectId(course_id)})

    return {
        "success": True,
        "data": course_to_public(updated_course),
        "message": "Course updated successfully",
    }


@router.delete("/{course_id}")
async def delete_course(course_id: str, user: dict = Depends(require_roles("admin"))):
    """Delete a course (Admin only)"""
    db = database.db

    if not ObjectId.is_valid(course_id):
        raise HTTPException(status_code=400, detail="Invalid course ID")

    result = await db.courses.delete_one({"_id": ObjectId(course_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Course not found")

    # Also delete related data
    await db.enrollments.delete_many({"courseId": course_id})
    await db.modules.delete_many({"courseId": course_id})

    return {"success": True, "data": None, "message": "Course deleted successfully"}


# -------- Course Statistics --------


@router.get("/{course_id}/stats")
async def get_course_stats(
    course_id: str, user: dict = Depends(require_roles("instructor", "admin"))
):
    """Get course statistics"""
    db = database.db

    if not ObjectId.is_valid(course_id):
        raise HTTPException(status_code=400, detail="Invalid course ID")

    course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    # Check ownership
    if user["role"] == "instructor" and course["instructorId"] != str(user["_id"]):
        raise HTTPException(status_code=403, detail="You don't own this course")

    # Get statistics
    total_students = await db.enrollments.count_documents({"courseId": course_id})

    assignments = await db.assignments.find({"courseId": course_id}).to_list(
        length=None
    )
    total_assignments = len(assignments)

    # Calculate average grade
    assignment_ids = [str(a["_id"]) for a in assignments]
    submissions = await db.submissions.find(
        {"assignmentId": {"$in": assignment_ids}}
    ).to_list(length=None)

    graded_submissions = [s for s in submissions if s.get("score") is not None]
    avg_grade = 0
    if graded_submissions:
        avg_grade = sum(s["score"] for s in graded_submissions) / len(
            graded_submissions
        )

    return {
        "success": True,
        "data": {
            "totalStudents": total_students,
            "totalAssignments": total_assignments,
            "averageGrade": round(avg_grade, 2),
            "completionRate": 0,
        },
        "message": "ok",
    }


# -------- Course Categories --------


@router.get("/categories")
async def get_categories(user: dict = Depends(get_current_user)):
    """Get all course categories"""
    db = database.db

    categories = await db.courses.distinct("category")
    return {"success": True, "data": categories, "message": "ok"}


# -------- Instructor's Courses --------


@router.get("/instructor/my-courses")
async def get_my_courses(
    status: str | None = Query(None),
    user: dict = Depends(require_roles("instructor", "admin")),
):
    """Get courses created by current instructor"""
    db = database.db

    query = {"instructorId": str(user["_id"])}
    if status:
        query["status"] = status

    cursor = db.courses.find(query).sort("createdAt", -1)
    courses = [course_to_public(c) async for c in cursor]

    return {"success": True, "data": courses, "message": "ok"}
