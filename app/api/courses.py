#api/courses.py
from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from datetime import datetime
from app.core.database import database
from app.api.deps import require_roles, get_current_user
from app.models.course import new_course_doc, course_to_public
from app.schemas.course import CreateCourseRequest
from app.schemas.course import CreateCourseRequest, UpdateCourseRequest

router = APIRouter(prefix="/courses", tags=["courses"])


@router.get("")
async def list_courses(user: dict = Depends(get_current_user)):
    db = database.db
    if user["role"] == "instructor":
        query = {"instructorId": str(user["_id"])}
    elif user["role"] == "admin":
        query = {}
    else:
        enrollments = await db.enrollments.find({"userId": str(user["_id"])}).to_list(
            length=None
        )
        course_ids = [ObjectId(e["courseId"]) for e in enrollments]
        query = {"_id": {"$in": course_ids}}

    cursor = db.courses.find(query).sort("createdAt", -1)
    courses = [course_to_public(c) async for c in cursor]

    instructor_ids = {c["instructorId"] for c in courses if c["instructorId"]}
    valid_ids = [ObjectId(i) for i in instructor_ids if ObjectId.is_valid(i)]
    instructor_names = {}
    if valid_ids:
        async for u in db.users.find({"_id": {"$in": valid_ids}}):
            instructor_names[str(u["_id"])] = u.get("name", "")

    # Real enrolled-student counts, computed from the enrollments collection
    # (courses.enrollmentCount is never kept in sync, so don't trust it).
    course_ids = [c["id"] for c in courses]
    counts: dict[str, int] = {}
    if course_ids:
        async for row in db.enrollments.aggregate(
            [
                {"$match": {"courseId": {"$in": course_ids}}},
                {"$group": {"_id": "$courseId", "count": {"$sum": 1}}},
            ]
        ):
            counts[row["_id"]] = row["count"]

    for c in courses:
        c["instructorName"] = instructor_names.get(c["instructorId"], "")
        c["studentCount"] = counts.get(c["id"], 0)

    return {"success": True, "data": courses, "message": "ok"}


@router.post("")
async def create_course(
    payload: CreateCourseRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db

    # instructors can only ever own their own courses;
    # admins pick the instructor from the form (payload.instructorId)
    if user["role"] == "instructor":
        instructor_id = str(user["_id"])
    else:
        instructor_id = payload.instructorId or str(user["_id"])

    doc = new_course_doc(
        title=payload.title,
        description=payload.description,
        instructor_id=instructor_id,
        category=payload.category,
        level=payload.level,
        duration_weeks=payload.durationWeeks,
        thumbnail=payload.thumbnail,
        objectives=payload.objectives,
        prerequisites=payload.prerequisites,
        status=payload.status,
    )
    result = await db.courses.insert_one(doc)
    doc["_id"] = result.inserted_id

    course = course_to_public(doc)
    if instructor_id and ObjectId.is_valid(instructor_id):
        instructor = await db.users.find_one({"_id": ObjectId(instructor_id)})
        course["instructorName"] = instructor.get("name", "") if instructor else ""

    return {"success": True, "data": course, "message": "course created"}
@router.get("/{course_id}")
async def get_course(
    course_id: str,
    user: dict = Depends(get_current_user),
):
    db = database.db
    if not ObjectId.is_valid(course_id):
        raise HTTPException(status_code=404, detail="Course not found")

    course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    # instructors can only view their own courses; students must be enrolled
    if user["role"] == "instructor" and course.get("instructorId") != str(user["_id"]):
        raise HTTPException(status_code=403, detail="Not allowed to view this course")
    if user["role"] == "student":
        enrolled = await db.enrollments.find_one(
            {"courseId": course_id, "userId": str(user["_id"])}
        )
        if not enrolled:
            raise HTTPException(status_code=403, detail="You are not enrolled in this course")

    result = course_to_public(course)
    if result["instructorId"] and ObjectId.is_valid(result["instructorId"]):
        instructor = await db.users.find_one({"_id": ObjectId(result["instructorId"])})
        result["instructorName"] = instructor.get("name", "") if instructor else ""
    result["studentCount"] = await db.enrollments.count_documents(
        {"courseId": course_id}
    )
    return {"success": True, "data": result, "message": "ok"}


@router.patch("/{course_id}")
@router.put("/{course_id}")
async def update_course(
    course_id: str,
    payload: UpdateCourseRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    # instructors can only edit their own courses
    if user["role"] == "instructor" and course.get("instructorId") != str(user["_id"]):
        raise HTTPException(status_code=403, detail="Not allowed to edit this course")

    updates = payload.dict(exclude_unset=True)
    if "durationWeeks" in updates:
        pass  # already camelCase, no remapping needed
    if updates:
        updates["updatedAt"] = datetime.utcnow()
        await db.courses.update_one({"_id": ObjectId(course_id)}, {"$set": updates})

    updated = await db.courses.find_one({"_id": ObjectId(course_id)})
    result = course_to_public(updated)
    if result["instructorId"] and ObjectId.is_valid(result["instructorId"]):
        instructor = await db.users.find_one({"_id": ObjectId(result["instructorId"])})
        result["instructorName"] = instructor.get("name", "") if instructor else ""
    result["studentCount"] = await db.enrollments.count_documents(
        {"courseId": course_id}
    )
    return {"success": True, "data": result, "message": "course updated"}


@router.delete("/{course_id}")
async def delete_course(
    course_id: str,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    if user["role"] == "instructor" and course.get("instructorId") != str(user["_id"]):
        raise HTTPException(status_code=403, detail="Not allowed to delete this course")

    await db.courses.delete_one({"_id": ObjectId(course_id)})
    return {"success": True, "data": None, "message": "course deleted"}













# #api/courses.py
# from fastapi import APIRouter, Depends, HTTPException
# from bson import ObjectId
# from datetime import datetime
# from app.core.database import database
# from app.api.deps import require_roles, get_current_user
# from app.models.course import new_course_doc, course_to_public
# from app.schemas.course import CreateCourseRequest, UpdateCourseRequest

# router = APIRouter(prefix="/courses", tags=["courses"])


# @router.get("")
# async def list_courses(user: dict = Depends(get_current_user)):
#     db = database.db
#     if user["role"] == "instructor":
#         query = {"instructorId": str(user["_id"])}
#     elif user["role"] == "admin":
#         query = {}
#     else:
#         enrollments = await db.enrollments.find({"userId": str(user["_id"])}).to_list(
#             length=None
#         )
#         course_ids = [ObjectId(e["courseId"]) for e in enrollments]
#         query = {"_id": {"$in": course_ids}}

#     cursor = db.courses.find(query).sort("createdAt", -1)
#     courses = [course_to_public(c) async for c in cursor]

#     instructor_ids = {c["instructorId"] for c in courses if c["instructorId"]}
#     valid_ids = [ObjectId(i) for i in instructor_ids if ObjectId.is_valid(i)]
#     instructor_names = {}
#     if valid_ids:
#         async for u in db.users.find({"_id": {"$in": valid_ids}}):
#             instructor_names[str(u["_id"])] = u.get("name", "")

#     for c in courses:
#         c["instructorName"] = instructor_names.get(c["instructorId"], "")

#     return {"success": True, "data": courses, "message": "ok"}


# @router.post("")
# async def create_course(
#     payload: CreateCourseRequest,
#     user: dict = Depends(require_roles("instructor", "admin")),
# ):
#     db = database.db

#     # instructors can only ever own their own courses;
#     # admins pick the instructor from the form (payload.instructorId)
#     if user["role"] == "instructor":
#         instructor_id = str(user["_id"])
#     else:
#         instructor_id = payload.instructorId or str(user["_id"])

#     doc = new_course_doc(
#         title=payload.title,
#         description=payload.description,
#         instructor_id=instructor_id,
#         category=payload.category,
#         level=payload.level,
#         duration_weeks=payload.durationWeeks,
#         thumbnail=payload.thumbnail,
#         objectives=payload.objectives,
#         prerequisites=payload.prerequisites,
#         status=payload.status,
#     )
#     result = await db.courses.insert_one(doc)
#     doc["_id"] = result.inserted_id

#     course = course_to_public(doc)
#     if instructor_id and ObjectId.is_valid(instructor_id):
#         instructor = await db.users.find_one({"_id": ObjectId(instructor_id)})
#         course["instructorName"] = instructor.get("name", "") if instructor else ""

#     # Admin assigned a course to an instructor → notify that instructor
#     if (
#         user["role"] == "admin"
#         and instructor_id
#         and instructor_id != str(user["_id"])
#     ):
#         admin_name = user.get("name") or "Admin"
#         try:
#             from app.api.notifications import notify_user

#             await notify_user(
#                 instructor_id,
#                 title=f"New course assigned: {payload.title}",
#                 body=f"{admin_name} assigned you as instructor for the course “{payload.title}”.",
#                 kind="system",
#                 link="/instructor/courses",
#                 course_id=str(result.inserted_id),
#             )
#         except Exception as exc:
#             print(f"[notifications] course create notify failed: {exc}")

#     return {"success": True, "data": course, "message": "course created"}


# @router.patch("/{course_id}")
# @router.put("/{course_id}")
# async def update_course(
#     course_id: str,
#     payload: UpdateCourseRequest,
#     user: dict = Depends(require_roles("instructor", "admin")),
# ):
#     db = database.db
#     course = await db.courses.find_one({"_id": ObjectId(course_id)})
#     if not course:
#         raise HTTPException(status_code=404, detail="Course not found")

#     # instructors can only edit their own courses
#     if user["role"] == "instructor" and course.get("instructorId") != str(user["_id"]):
#         raise HTTPException(status_code=403, detail="Not allowed to edit this course")

#     old_instructor_id = str(course.get("instructorId") or "")
#     updates = payload.dict(exclude_unset=True)
#     if updates:
#         updates["updatedAt"] = datetime.utcnow()
#         await db.courses.update_one({"_id": ObjectId(course_id)}, {"$set": updates})

#     updated = await db.courses.find_one({"_id": ObjectId(course_id)})
#     result = course_to_public(updated)
#     if result["instructorId"] and ObjectId.is_valid(result["instructorId"]):
#         instructor = await db.users.find_one({"_id": ObjectId(result["instructorId"])})
#         result["instructorName"] = instructor.get("name", "") if instructor else ""

#     # Admin re-assigned instructor → notify the new instructor
#     new_instructor_id = str(updates.get("instructorId") or result.get("instructorId") or "")
#     if (
#         user["role"] == "admin"
#         and new_instructor_id
#         and new_instructor_id != old_instructor_id
#         and new_instructor_id != str(user["_id"])
#     ):
#         admin_name = user.get("name") or "Admin"
#         course_title = result.get("title") or "a course"
#         try:
#             from app.api.notifications import notify_user

#             await notify_user(
#                 new_instructor_id,
#                 title=f"Course assigned: {course_title}",
#                 body=f"{admin_name} assigned you as instructor for “{course_title}”.",
#                 kind="system",
#                 link="/instructor/courses",
#                 course_id=course_id,
#             )
#         except Exception as exc:
#             print(f"[notifications] course update notify failed: {exc}")

#     return {"success": True, "data": result, "message": "course updated"}


# @router.delete("/{course_id}")
# async def delete_course(
#     course_id: str,
#     user: dict = Depends(require_roles("instructor", "admin")),
# ):
#     db = database.db
#     course = await db.courses.find_one({"_id": ObjectId(course_id)})
#     if not course:
#         raise HTTPException(status_code=404, detail="Course not found")

#     if user["role"] == "instructor" and course.get("instructorId") != str(user["_id"]):
#         raise HTTPException(status_code=403, detail="Not allowed to delete this course")

#     instructor_id = str(course.get("instructorId") or "")
#     course_title = course.get("title") or "a course"

#     await db.courses.delete_one({"_id": ObjectId(course_id)})

#     # Admin deleted course → notify the instructor
#     if (
#         user["role"] == "admin"
#         and instructor_id
#         and instructor_id != str(user["_id"])
#     ):
#         admin_name = user.get("name") or "Admin"
#         try:
#             from app.api.notifications import notify_user

#             await notify_user(
#                 instructor_id,
#                 title=f"Course removed: {course_title}",
#                 body=f"{admin_name} removed the course “{course_title}” from the system.",
#                 kind="system",
#                 link="/instructor/courses",
#             )
#         except Exception as exc:
#             print(f"[notifications] course delete notify failed: {exc}")

#     return {"success": True, "data": None, "message": "course deleted"}











# #api/courses.py
# from fastapi import APIRouter, Depends, HTTPException
# from bson import ObjectId
# from datetime import datetime
# from app.core.database import database
# from app.api.deps import require_roles, get_current_user
# from app.models.course import new_course_doc, course_to_public
# from app.schemas.course import CreateCourseRequest
# from app.schemas.course import CreateCourseRequest, UpdateCourseRequest

# router = APIRouter(prefix="/courses", tags=["courses"])


# @router.get("")
# async def list_courses(user: dict = Depends(get_current_user)):
#     db = database.db
#     if user["role"] == "instructor":
#         query = {"instructorId": str(user["_id"])}
#     elif user["role"] == "admin":
#         query = {}
#     else:
#         enrollments = await db.enrollments.find({"userId": str(user["_id"])}).to_list(
#             length=None
#         )
#         course_ids = [ObjectId(e["courseId"]) for e in enrollments]
#         query = {"_id": {"$in": course_ids}}

#     cursor = db.courses.find(query).sort("createdAt", -1)
#     courses = [course_to_public(c) async for c in cursor]

#     instructor_ids = {c["instructorId"] for c in courses if c["instructorId"]}
#     valid_ids = [ObjectId(i) for i in instructor_ids if ObjectId.is_valid(i)]
#     instructor_names = {}
#     if valid_ids:
#         async for u in db.users.find({"_id": {"$in": valid_ids}}):
#             instructor_names[str(u["_id"])] = u.get("name", "")

#     for c in courses:
#         c["instructorName"] = instructor_names.get(c["instructorId"], "")

#     return {"success": True, "data": courses, "message": "ok"}


# @router.post("")
# async def create_course(
#     payload: CreateCourseRequest,
#     user: dict = Depends(require_roles("instructor", "admin")),
# ):
#     db = database.db

#     # instructors can only ever own their own courses;
#     # admins pick the instructor from the form (payload.instructorId)
#     if user["role"] == "instructor":
#         instructor_id = str(user["_id"])
#     else:
#         instructor_id = payload.instructorId or str(user["_id"])

#     doc = new_course_doc(
#         title=payload.title,
#         description=payload.description,
#         instructor_id=instructor_id,
#         category=payload.category,
#         level=payload.level,
#         duration_weeks=payload.durationWeeks,
#         thumbnail=payload.thumbnail,
#         objectives=payload.objectives,
#         prerequisites=payload.prerequisites,
#         status=payload.status,
#     )
#     result = await db.courses.insert_one(doc)
#     doc["_id"] = result.inserted_id

#     course = course_to_public(doc)
#     if instructor_id and ObjectId.is_valid(instructor_id):
#         instructor = await db.users.find_one({"_id": ObjectId(instructor_id)})
#         course["instructorName"] = instructor.get("name", "") if instructor else ""

#     return {"success": True, "data": course, "message": "course created"}
# @router.patch("/{course_id}")
# @router.put("/{course_id}")
# async def update_course(
#     course_id: str,
#     payload: UpdateCourseRequest,
#     user: dict = Depends(require_roles("instructor", "admin")),
# ):
#     db = database.db
#     course = await db.courses.find_one({"_id": ObjectId(course_id)})
#     if not course:
#         raise HTTPException(status_code=404, detail="Course not found")

#     # instructors can only edit their own courses
#     if user["role"] == "instructor" and course.get("instructorId") != str(user["_id"]):
#         raise HTTPException(status_code=403, detail="Not allowed to edit this course")

#     updates = payload.dict(exclude_unset=True)
#     if "durationWeeks" in updates:
#         pass  # already camelCase, no remapping needed
#     if updates:
#         updates["updatedAt"] = datetime.utcnow()
#         await db.courses.update_one({"_id": ObjectId(course_id)}, {"$set": updates})

#     updated = await db.courses.find_one({"_id": ObjectId(course_id)})
#     result = course_to_public(updated)
#     if result["instructorId"] and ObjectId.is_valid(result["instructorId"]):
#         instructor = await db.users.find_one({"_id": ObjectId(result["instructorId"])})
#         result["instructorName"] = instructor.get("name", "") if instructor else ""
#     return {"success": True, "data": result, "message": "course updated"}


# @router.delete("/{course_id}")
# async def delete_course(
#     course_id: str,
#     user: dict = Depends(require_roles("instructor", "admin")),
# ):
#     db = database.db
#     course = await db.courses.find_one({"_id": ObjectId(course_id)})
#     if not course:
#         raise HTTPException(status_code=404, detail="Course not found")

#     if user["role"] == "instructor" and course.get("instructorId") != str(user["_id"]):
#         raise HTTPException(status_code=403, detail="Not allowed to delete this course")

#     await db.courses.delete_one({"_id": ObjectId(course_id)})
#     return {"success": True, "data": None, "message": "course deleted"}



