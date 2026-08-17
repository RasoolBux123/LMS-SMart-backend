"""
Notifications API.

- Instructor publishes assignment/quiz/exam/project → enrolled students get notified
- Admin creates/assigns course or enrolls student → assigned instructor gets notified
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user
from app.core.database import database

router = APIRouter(prefix="/notifications", tags=["notifications"])

KIND_LABELS = {
    "assignment": "Assignment",
    "quiz": "Quiz",
    "exam": "Exam",
    "project": "Project",
}


def _to_iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def notification_to_public(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "title": doc.get("title", ""),
        "body": doc.get("body", ""),
        "kind": doc.get("kind", "system"),
        "createdAt": _to_iso(doc.get("createdAt")),
        "read": bool(doc.get("read", False)),
        "link": doc.get("link"),
        "courseworkId": doc.get("courseworkId"),
        "courseworkKind": doc.get("courseworkKind"),
        "courseId": doc.get("courseId"),
    }


async def create_notification(
    *,
    user_id: str,
    title: str,
    body: str,
    kind: str = "system",
    link: Optional[str] = None,
    course_id: Optional[str] = None,
    coursework_id: Optional[str] = None,
    coursework_kind: Optional[str] = None,
) -> Optional[str]:
    """Insert a single notification for one user. Returns inserted id or None."""
    if not user_id:
        return None
    db = database.db
    doc = {
        "userId": str(user_id),
        "title": title,
        "body": body,
        "kind": kind,
        "read": False,
        "link": link,
        "courseId": str(course_id) if course_id else None,
        "courseworkId": str(coursework_id) if coursework_id else None,
        "courseworkKind": coursework_kind,
        "createdAt": datetime.utcnow(),
    }
    result = await db.notifications.insert_one(doc)
    return str(result.inserted_id)


async def notify_user(
    user_id: str,
    title: str,
    body: str,
    *,
    kind: str = "system",
    link: Optional[str] = None,
    course_id: Optional[str] = None,
) -> None:
    """Convenience wrapper — never raises."""
    try:
        await create_notification(
            user_id=user_id,
            title=title,
            body=body,
            kind=kind,
            link=link,
            course_id=course_id,
        )
    except Exception as exc:
        print(f"[notifications] notify_user failed: {exc}")


async def notify_enrolled_students(
    *,
    course_id: str,
    coursework_id: str,
    coursework_kind: str,
    title: str,
    instructor_name: str = "Instructor",
) -> int:
    """
    Create one notification per enrolled student for a newly published item.
    Returns the number of notifications created.
    """
    db = database.db
    if not course_id or not coursework_id:
        return 0

    label = KIND_LABELS.get(coursework_kind, "Item")
    plural = {
        "assignment": "assignments",
        "quiz": "quizzes",
        "exam": "exams",
        "project": "projects",
    }.get(coursework_kind, f"{coursework_kind}s")

    notif_title = f"New {label}: {title}"
    notif_body = f"{instructor_name} published a new {label.lower()} for your course."
    link = f"/student/{plural}"

    cursor = db.enrollments.find(
        {
            "courseId": course_id,
            "$or": [
                {"status": "active"},
                {"status": {"$exists": False}},
                {"status": None},
            ],
        }
    )

    docs = []
    now = datetime.utcnow()
    seen: set[str] = set()

    async for enrollment in cursor:
        user_id = str(enrollment.get("userId") or "")
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        docs.append(
            {
                "userId": user_id,
                "title": notif_title,
                "body": notif_body,
                "kind": "deadline",
                "read": False,
                "link": link,
                "courseworkId": str(coursework_id),
                "courseworkKind": coursework_kind,
                "courseId": str(course_id),
                "createdAt": now,
            }
        )

    if not docs:
        return 0

    result = await db.notifications.insert_many(docs)
    return len(result.inserted_ids)


@router.get("")
async def list_notifications(
    limit: int = Query(30, ge=1, le=100),
    unreadOnly: bool = Query(False),
    user: dict = Depends(get_current_user),
):
    db = database.db
    query: dict = {"userId": str(user["_id"])}
    if unreadOnly:
        query["read"] = False

    cursor = (
        db.notifications.find(query)
        .sort("createdAt", -1)
        .limit(limit)
    )
    items = []
    async for doc in cursor:
        items.append(notification_to_public(doc))

    unread_count = await db.notifications.count_documents(
        {"userId": str(user["_id"]), "read": False}
    )

    return {
        "success": True,
        "data": items,
        "unreadCount": unread_count,
        "message": "ok",
    }


@router.patch("/{notification_id}/read")
async def mark_read(
    notification_id: str,
    user: dict = Depends(get_current_user),
):
    db = database.db
    try:
        oid = ObjectId(notification_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail="Invalid id")

    result = await db.notifications.update_one(
        {"_id": oid, "userId": str(user["_id"])},
        {"$set": {"read": True}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")

    return {"success": True, "message": "marked as read"}


@router.patch("/read-all")
async def mark_all_read(user: dict = Depends(get_current_user)):
    db = database.db
    result = await db.notifications.update_many(
        {"userId": str(user["_id"]), "read": False},
        {"$set": {"read": True}},
    )
    return {
        "success": True,
        "data": {"updated": result.modified_count},
        "message": "all marked as read",
    }


# """
# Notifications API.

# When an instructor publishes an assignment / quiz / exam / project,
# every enrolled student in that course receives a notification.
# """

# from __future__ import annotations

# from datetime import datetime
# from typing import Optional

# from bson import ObjectId
# from bson.errors import InvalidId
# from fastapi import APIRouter, Depends, HTTPException, Query
# from pydantic import BaseModel

# from app.api.deps import get_current_user
# from app.core.database import database

# router = APIRouter(prefix="/notifications", tags=["notifications"])

# KIND_LABELS = {
#     "assignment": "Assignment",
#     "quiz": "Quiz",
#     "exam": "Exam",
#     "project": "Project",
# }


# def _to_iso(value) -> Optional[str]:
#     if value is None:
#         return None
#     if isinstance(value, datetime):
#         return value.isoformat()
#     return str(value)


# def notification_to_public(doc: dict) -> dict:
#     return {
#         "id": str(doc["_id"]),
#         "title": doc.get("title", ""),
#         "body": doc.get("body", ""),
#         "kind": doc.get("kind", "system"),
#         "createdAt": _to_iso(doc.get("createdAt")),
#         "read": bool(doc.get("read", False)),
#         "link": doc.get("link"),
#         "courseworkId": doc.get("courseworkId"),
#         "courseworkKind": doc.get("courseworkKind"),
#         "courseId": doc.get("courseId"),
#     }


# async def notify_enrolled_students(
#     *,
#     course_id: str,
#     coursework_id: str,
#     coursework_kind: str,
#     title: str,
#     instructor_name: str = "Instructor",
# ) -> int:
#     """
#     Create one notification per enrolled student for a newly published item.
#     Returns the number of notifications created.
#     """
#     db = database.db
#     if not course_id or not coursework_id:
#         return 0

#     label = KIND_LABELS.get(coursework_kind, "Item")
#     plural = {
#         "assignment": "assignments",
#         "quiz": "quizzes",
#         "exam": "exams",
#         "project": "projects",
#     }.get(coursework_kind, f"{coursework_kind}s")

#     notif_title = f"New {label}: {title}"
#     notif_body = f"{instructor_name} published a new {label.lower()} for your course."
#     link = f"/student/{plural}"

#     cursor = db.enrollments.find(
#         {
#             "courseId": course_id,
#             "$or": [
#                 {"status": "active"},
#                 {"status": {"$exists": False}},
#                 {"status": None},
#             ],
#         }
#     )

#     docs = []
#     now = datetime.utcnow()
#     seen: set[str] = set()

#     async for enrollment in cursor:
#         user_id = str(enrollment.get("userId") or "")
#         if not user_id or user_id in seen:
#             continue
#         seen.add(user_id)
#         docs.append(
#             {
#                 "userId": user_id,
#                 "title": notif_title,
#                 "body": notif_body,
#                 "kind": "deadline",
#                 "read": False,
#                 "link": link,
#                 "courseworkId": str(coursework_id),
#                 "courseworkKind": coursework_kind,
#                 "courseId": str(course_id),
#                 "createdAt": now,
#             }
#         )

#     if not docs:
#         return 0

#     result = await db.notifications.insert_many(docs)
#     return len(result.inserted_ids)


# @router.get("")
# async def list_notifications(
#     limit: int = Query(30, ge=1, le=100),
#     unreadOnly: bool = Query(False),
#     user: dict = Depends(get_current_user),
# ):
#     db = database.db
#     query: dict = {"userId": str(user["_id"])}
#     if unreadOnly:
#         query["read"] = False

#     cursor = (
#         db.notifications.find(query)
#         .sort("createdAt", -1)
#         .limit(limit)
#     )
#     items = []
#     async for doc in cursor:
#         items.append(notification_to_public(doc))

#     unread_count = await db.notifications.count_documents(
#         {"userId": str(user["_id"]), "read": False}
#     )

#     return {
#         "success": True,
#         "data": items,
#         "unreadCount": unread_count,
#         "message": "ok",
#     }


# @router.patch("/{notification_id}/read")
# async def mark_read(
#     notification_id: str,
#     user: dict = Depends(get_current_user),
# ):
#     db = database.db
#     try:
#         oid = ObjectId(notification_id)
#     except (InvalidId, TypeError):
#         raise HTTPException(status_code=400, detail="Invalid id")

#     result = await db.notifications.update_one(
#         {"_id": oid, "userId": str(user["_id"])},
#         {"$set": {"read": True}},
#     )
#     if result.matched_count == 0:
#         raise HTTPException(status_code=404, detail="Notification not found")

#     return {"success": True, "message": "marked as read"}


# @router.patch("/read-all")
# async def mark_all_read(user: dict = Depends(get_current_user)):
#     db = database.db
#     result = await db.notifications.update_many(
#         {"userId": str(user["_id"]), "read": False},
#         {"$set": {"read": True}},
#     )
#     return {
#         "success": True,
#         "data": {"updated": result.modified_count},
#         "message": "all marked as read",
#     }