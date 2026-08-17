from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from bson import ObjectId
from datetime import datetime

from app.core.database import database
from app.api.deps import get_current_user, require_roles

router = APIRouter(prefix="/enrollments", tags=["enrollments"])


class EnrollRequest(BaseModel):
    courseId: str
    userId: str


def enrollment_to_public(doc: dict, student: dict | None = None) -> dict:
    data = {
        "id": str(doc["_id"]),
        "userId": doc["userId"],
        "courseId": doc["courseId"],
        "status": doc.get("status", "active"),
        "enrolledAt": doc.get("enrolledAt"),
    }
    if student:
        data["student"] = {
            "id": str(student["_id"]),
            "name": student.get("name"),
            "email": student.get("email"),
        }
    return data


async def _instructor_can_access_course(db, user: dict, course: dict, course_id: str) -> bool:
    if user["role"] == "admin":
        return True
    uid = str(user["_id"])
    if course.get("instructorId") == uid:
        return True
    for coll in ("assignments", "quizzes", "exams", "projects"):
        found = await db[coll].find_one(
            {"courseId": course_id, "instructorId": uid}
        )
        if found:
            return True
    return False


@router.post("")
async def enroll_student(
    payload: EnrollRequest,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    if not ObjectId.is_valid(payload.courseId) or not ObjectId.is_valid(payload.userId):
        raise HTTPException(status_code=400, detail="Invalid id")

    course = await db.courses.find_one({"_id": ObjectId(payload.courseId)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    if not await _instructor_can_access_course(db, user, course, payload.courseId):
        raise HTTPException(status_code=403, detail="Not your course")

    student = await db.users.find_one(
        {"_id": ObjectId(payload.userId), "role": "student"}
    )
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    existing = await db.enrollments.find_one(
        {"courseId": payload.courseId, "userId": payload.userId}
    )
    if existing:
        raise HTTPException(status_code=400, detail="Already enrolled")

    doc = {
        "courseId": payload.courseId,
        "userId": payload.userId,
        "status": "active",
        "enrolledAt": datetime.utcnow(),
    }
    result = await db.enrollments.insert_one(doc)
    doc["_id"] = result.inserted_id

    # Notify course instructor when admin enrolls a student
    instructor_id = str(course.get("instructorId") or "")
    if (
        user["role"] == "admin"
        and instructor_id
        and instructor_id != str(user["_id"])
        and ObjectId.is_valid(instructor_id)
    ):
        admin_name = user.get("name") or "Admin"
        student_name = student.get("name") or "A student"
        course_title = course.get("title") or "your course"
        try:
            from app.api.notifications import notify_user

            await notify_user(
                instructor_id,
                title=f"New enrollment: {student_name}",
                body=f"{admin_name} enrolled {student_name} in “{course_title}”.",
                kind="system",
                link="/instructor/students",
                course_id=payload.courseId,
            )
        except Exception as exc:
            print(f"[notifications] enroll notify failed: {exc}")

    return {
        "success": True,
        "data": enrollment_to_public(doc, student),
        "message": "enrolled",
    }


@router.get("/course/{course_id}")
async def list_course_enrollments(
    course_id: str,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    course = await db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    if not await _instructor_can_access_course(db, user, course, course_id):
        raise HTTPException(status_code=403, detail="Not your course")

    cursor = db.enrollments.find({"courseId": course_id}).sort("enrolledAt", -1)
    rows = []
    async for e in cursor:
        student = None
        if ObjectId.is_valid(e.get("userId", "")):
            student = await db.users.find_one({"_id": ObjectId(e["userId"])})
        rows.append(enrollment_to_public(e, student))
    return {"success": True, "data": rows, "message": "ok"}


@router.get("/me")
async def my_enrollments(user: dict = Depends(require_roles("student"))):
    db = database.db
    cursor = db.enrollments.find({"userId": str(user["_id"])}).sort(
        "enrolledAt", -1
    )
    rows = [enrollment_to_public(e) async for e in cursor]
    return {"success": True, "data": rows, "message": "ok"}


@router.delete("/{enrollment_id}")
async def unenroll(
    enrollment_id: str,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    db = database.db
    enrollment = await db.enrollments.find_one({"_id": ObjectId(enrollment_id)})
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    course = await db.courses.find_one({"_id": ObjectId(enrollment["courseId"])})
    if course and not await _instructor_can_access_course(
        db, user, course, enrollment["courseId"]
    ):
        raise HTTPException(status_code=403, detail="Not your course")

    await db.enrollments.delete_one({"_id": ObjectId(enrollment_id)})
    return {"success": True, "data": None, "message": "unenrolled"}


@router.get("/my-students")
async def my_students(user: dict = Depends(require_roles("instructor", "admin"))):
    """
    Students enrolled in the signed-in instructor's courses.
    Includes courses owned by instructor OR where they have coursework.
    """
    db = database.db
    uid = str(user["_id"])

    if user["role"] == "instructor":
        course_ids = set()
        async for c in db.courses.find({"instructorId": uid}):
            course_ids.add(str(c["_id"]))
        # also courses where this instructor has any assignment/quiz/exam/project
        for coll in ("assignments", "quizzes", "exams", "projects"):
            async for item in db[coll].find({"instructorId": uid}):
                if item.get("courseId"):
                    course_ids.add(item["courseId"])
        course_ids = list(course_ids)
    else:
        course_ids = [str(c["_id"]) async for c in db.courses.find({})]

    if not course_ids:
        return {"success": True, "data": [], "message": "ok"}

    course_titles = {}
    for cid in course_ids:
        if ObjectId.is_valid(cid):
            c = await db.courses.find_one({"_id": ObjectId(cid)})
            if c:
                course_titles[cid] = c.get("title", "")

    cursor = db.enrollments.find({"courseId": {"$in": course_ids}})
    by_student: dict[str, dict] = {}
    async for e in cursor:
        sid = e["userId"]
        if sid not in by_student:
            student = None
            if ObjectId.is_valid(sid):
                student = await db.users.find_one({"_id": ObjectId(sid)})
            if not student:
                continue
            by_student[sid] = {
                "id": str(student["_id"]),
                "name": student.get("name", ""),
                "email": student.get("email", ""),
                "role": student.get("role", "student"),
                "status": student.get("status", "active"),
                "createdAt": student.get("createdAt"),
                "courses": [],
                "enrollmentCount": 0,
            }
        cid = e["courseId"]
        title = course_titles.get(cid, cid)
        entry = by_student[sid]
        if not any(x["id"] == cid for x in entry["courses"]):
            entry["courses"].append({"id": cid, "title": title})
            entry["enrollmentCount"] = len(entry["courses"])

    rows = sorted(by_student.values(), key=lambda r: r["name"].lower())
    return {"success": True, "data": rows, "message": "ok"}


@router.get("/user/{user_id}")
async def list_user_enrollments(
    user_id: str,
    user: dict = Depends(require_roles("instructor", "admin")),
):
    """Admin/instructor: list all courses a student is enrolled in."""
    db = database.db
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status_code=400, detail="Invalid user id")

    cursor = db.enrollments.find({"userId": user_id}).sort("enrolledAt", -1)
    rows = []
    async for e in cursor:
        course = None
        if ObjectId.is_valid(e["courseId"]):
            course = await db.courses.find_one({"_id": ObjectId(e["courseId"])})
        data = enrollment_to_public(e)
        if course:
            data["course"] = {
                "id": str(course["_id"]),
                "title": course.get("title", ""),
                "instructorId": course.get("instructorId", ""),
                "status": course.get("status", ""),
            }
        rows.append(data)
    return {"success": True, "data": rows, "message": "ok"}







# from fastapi import APIRouter, Depends, HTTPException
# from pydantic import BaseModel
# from bson import ObjectId
# from datetime import datetime

# from app.core.database import database
# from app.api.deps import get_current_user, require_roles

# router = APIRouter(prefix="/enrollments", tags=["enrollments"])


# class EnrollRequest(BaseModel):
#     courseId: str
#     userId: str


# def enrollment_to_public(doc: dict, student: dict | None = None) -> dict:
#     data = {
#         "id": str(doc["_id"]),
#         "userId": doc["userId"],
#         "courseId": doc["courseId"],
#         "status": doc.get("status", "active"),
#         "enrolledAt": doc.get("enrolledAt"),
#     }
#     if student:
#         data["student"] = {
#             "id": str(student["_id"]),
#             "name": student.get("name"),
#             "email": student.get("email"),
#         }
#     return data


# async def _instructor_can_access_course(db, user: dict, course: dict, course_id: str) -> bool:
#     if user["role"] == "admin":
#         return True
#     uid = str(user["_id"])
#     if course.get("instructorId") == uid:
#         return True
#     for coll in ("assignments", "quizzes", "exams", "projects"):
#         found = await db[coll].find_one(
#             {"courseId": course_id, "instructorId": uid}
#         )
#         if found:
#             return True
#     return False


# @router.post("")
# async def enroll_student(
#     payload: EnrollRequest,
#     user: dict = Depends(require_roles("instructor", "admin")),
# ):
#     db = database.db
#     if not ObjectId.is_valid(payload.courseId) or not ObjectId.is_valid(payload.userId):
#         raise HTTPException(status_code=400, detail="Invalid id")

#     course = await db.courses.find_one({"_id": ObjectId(payload.courseId)})
#     if not course:
#         raise HTTPException(status_code=404, detail="Course not found")

#     if not await _instructor_can_access_course(db, user, course, payload.courseId):
#         raise HTTPException(status_code=403, detail="Not your course")

#     student = await db.users.find_one(
#         {"_id": ObjectId(payload.userId), "role": "student"}
#     )
#     if not student:
#         raise HTTPException(status_code=404, detail="Student not found")

#     existing = await db.enrollments.find_one(
#         {"courseId": payload.courseId, "userId": payload.userId}
#     )
#     if existing:
#         raise HTTPException(status_code=400, detail="Already enrolled")

#     doc = {
#         "courseId": payload.courseId,
#         "userId": payload.userId,
#         "status": "active",
#         "enrolledAt": datetime.utcnow(),
#     }
#     result = await db.enrollments.insert_one(doc)
#     doc["_id"] = result.inserted_id
#     return {
#         "success": True,
#         "data": enrollment_to_public(doc, student),
#         "message": "enrolled",
#     }


# @router.get("/course/{course_id}")
# async def list_course_enrollments(
#     course_id: str,
#     user: dict = Depends(require_roles("instructor", "admin")),
# ):
#     db = database.db
#     course = await db.courses.find_one({"_id": ObjectId(course_id)})
#     if not course:
#         raise HTTPException(status_code=404, detail="Course not found")

#     if not await _instructor_can_access_course(db, user, course, course_id):
#         raise HTTPException(status_code=403, detail="Not your course")

#     cursor = db.enrollments.find({"courseId": course_id}).sort("enrolledAt", -1)
#     rows = []
#     async for e in cursor:
#         student = None
#         if ObjectId.is_valid(e.get("userId", "")):
#             student = await db.users.find_one({"_id": ObjectId(e["userId"])})
#         rows.append(enrollment_to_public(e, student))
#     return {"success": True, "data": rows, "message": "ok"}


# @router.get("/me")
# async def my_enrollments(user: dict = Depends(require_roles("student"))):
#     db = database.db
#     cursor = db.enrollments.find({"userId": str(user["_id"])}).sort(
#         "enrolledAt", -1
#     )
#     rows = [enrollment_to_public(e) async for e in cursor]
#     return {"success": True, "data": rows, "message": "ok"}


# @router.delete("/{enrollment_id}")
# async def unenroll(
#     enrollment_id: str,
#     user: dict = Depends(require_roles("instructor", "admin")),
# ):
#     db = database.db
#     enrollment = await db.enrollments.find_one({"_id": ObjectId(enrollment_id)})
#     if not enrollment:
#         raise HTTPException(status_code=404, detail="Enrollment not found")

#     course = await db.courses.find_one({"_id": ObjectId(enrollment["courseId"])})
#     if course and not await _instructor_can_access_course(
#         db, user, course, enrollment["courseId"]
#     ):
#         raise HTTPException(status_code=403, detail="Not your course")

#     await db.enrollments.delete_one({"_id": ObjectId(enrollment_id)})
#     return {"success": True, "data": None, "message": "unenrolled"}


# @router.get("/my-students")
# async def my_students(user: dict = Depends(require_roles("instructor", "admin"))):
#     """
#     Students enrolled in the signed-in instructor's courses.
#     Includes courses owned by instructor OR where they have coursework.
#     """
#     db = database.db
#     uid = str(user["_id"])

#     if user["role"] == "instructor":
#         course_ids = set()
#         async for c in db.courses.find({"instructorId": uid}):
#             course_ids.add(str(c["_id"]))
#         # also courses where this instructor has any assignment/quiz/exam/project
#         for coll in ("assignments", "quizzes", "exams", "projects"):
#             async for item in db[coll].find({"instructorId": uid}):
#                 if item.get("courseId"):
#                     course_ids.add(item["courseId"])
#         course_ids = list(course_ids)
#     else:
#         course_ids = [str(c["_id"]) async for c in db.courses.find({})]

#     if not course_ids:
#         return {"success": True, "data": [], "message": "ok"}

#     course_titles = {}
#     for cid in course_ids:
#         if ObjectId.is_valid(cid):
#             c = await db.courses.find_one({"_id": ObjectId(cid)})
#             if c:
#                 course_titles[cid] = c.get("title", "")

#     cursor = db.enrollments.find({"courseId": {"$in": course_ids}})
#     by_student: dict[str, dict] = {}
#     async for e in cursor:
#         sid = e["userId"]
#         if sid not in by_student:
#             student = None
#             if ObjectId.is_valid(sid):
#                 student = await db.users.find_one({"_id": ObjectId(sid)})
#             if not student:
#                 continue
#             by_student[sid] = {
#                 "id": str(student["_id"]),
#                 "name": student.get("name", ""),
#                 "email": student.get("email", ""),
#                 "role": student.get("role", "student"),
#                 "status": student.get("status", "active"),
#                 "createdAt": student.get("createdAt"),
#                 "courses": [],
#                 "enrollmentCount": 0,
#             }
#         cid = e["courseId"]
#         title = course_titles.get(cid, cid)
#         entry = by_student[sid]
#         if not any(x["id"] == cid for x in entry["courses"]):
#             entry["courses"].append({"id": cid, "title": title})
#             entry["enrollmentCount"] = len(entry["courses"])

#     rows = sorted(by_student.values(), key=lambda r: r["name"].lower())
#     return {"success": True, "data": rows, "message": "ok"}


# @router.get("/user/{user_id}")
# async def list_user_enrollments(
#     user_id: str,
#     user: dict = Depends(require_roles("instructor", "admin")),
# ):
#     """Admin/instructor: list all courses a student is enrolled in."""
#     db = database.db
#     if not ObjectId.is_valid(user_id):
#         raise HTTPException(status_code=400, detail="Invalid user id")

#     cursor = db.enrollments.find({"userId": user_id}).sort("enrolledAt", -1)
#     rows = []
#     async for e in cursor:
#         course = None
#         if ObjectId.is_valid(e["courseId"]):
#             course = await db.courses.find_one({"_id": ObjectId(e["courseId"])})
#         data = enrollment_to_public(e)
#         if course:
#             data["course"] = {
#                 "id": str(course["_id"]),
#                 "title": course.get("title", ""),
#                 "instructorId": course.get("instructorId", ""),
#                 "status": course.get("status", ""),
#             }
#         rows.append(data)
#     return {"success": True, "data": rows, "message": "ok"}








