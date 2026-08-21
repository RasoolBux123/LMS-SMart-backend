# app/services/notifications.py
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from bson import ObjectId

from app.core.database import database
from app.schemas.notification import NotificationOut, NotificationType, NotificationSeverity


def get_notifications_collection():
    return database.get_db()["notifications"]


# ============================================================
# Core create / fetch
# ============================================================
async def create_notification(
    user_id: str,
    type: NotificationType,
    severity: NotificationSeverity,
    title: str,
    message: str,
    link: Optional[str] = None,
    course_id: Optional[str] = None,
    dedupe_key: Optional[str] = None,
) -> Optional[dict]:
    """Create a notification. If dedupe_key is given, skip if an unread one
    with the same key already exists (avoids spamming the same alert)."""
    collection = get_notifications_collection()

    if dedupe_key:
        existing = await collection.find_one({
            "user_id": user_id,
            "dedupe_key": dedupe_key,
            "read": False,
        })
        if existing:
            return None  # already alerted, don't duplicate

    doc = {
        "user_id": user_id,
        "type": type,
        "severity": severity,
        "title": title,
        "message": message,
        "link": link,
        "course_id": course_id,
        "read": False,
        "dedupe_key": dedupe_key,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await collection.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    return doc


async def get_notifications(user_id: str, limit: int = 20) -> List[dict]:
    collection = get_notifications_collection()
    cursor = collection.find({"user_id": user_id}).sort("created_at", -1).limit(limit)
    results = []
    async for doc in cursor:
        doc["id"] = str(doc["_id"])
        del doc["_id"]
        results.append(doc)
    return results


async def mark_as_read(user_id: str, notification_ids: List[str]) -> int:
    collection = get_notifications_collection()
    object_ids = [ObjectId(nid) for nid in notification_ids if ObjectId.is_valid(nid)]
    result = await collection.update_many(
        {"_id": {"$in": object_ids}, "user_id": user_id},
        {"$set": {"read": True}},
    )
    return result.modified_count


async def get_unread_count(user_id: str) -> int:
    collection = get_notifications_collection()
    return await collection.count_documents({"user_id": user_id, "read": False})


# ============================================================
# Risk alert generation (from AI insights)
# ============================================================
async def generate_risk_alert_notifications(course_id: str, instructor_email: str):
    """Call this after bulk/individual AI insight generation. Notifies the
    instructor about newly at-risk/failure-risk students."""
    from app.services.ai_insights import get_all_insights_for_course

    insights = await get_all_insights_for_course(course_id)
    created = []

    for insight in insights:
        risk = insight.get("risk_category")
        if risk not in ("at_risk", "failure_risk"):
            continue

        severity: NotificationSeverity = "critical" if risk == "failure_risk" else "warning"
        student_id = insight["student_id"]

        notif = await create_notification(
            user_id=instructor_email,
            type="risk_alert",
            severity=severity,
            title=f"{'Failure risk' if risk == 'failure_risk' else 'At risk'}: {student_id}",
            message=insight.get("instructor_insight", ""),
            link="/instructor/ai-insights",
            course_id=course_id,
            dedupe_key=f"risk:{student_id}:{course_id}:{risk}",
        )
        if notif:
            created.append(notif)

    return created


# ============================================================
# Deadline reminder generation
# ============================================================
async def generate_deadline_reminders(
    student_id: str,
    course_id: str,
    items: List[dict],  # [{ "title": ..., "type": "Assignment"/"Quiz"/"Exam"/"Project", "due_date": ISO str, "link": ... }]
):
    """Call this daily (or on page load) to remind a student about upcoming deadlines
    within the next 48 hours that they haven't submitted yet."""
    now = datetime.now(timezone.utc)
    created = []

    for item in items:
        try:
            due = datetime.fromisoformat(item["due_date"])
        except (KeyError, ValueError):
            continue

        hours_left = (due - now).total_seconds() / 3600
        if 0 < hours_left <= 48:
            severity: NotificationSeverity = "critical" if hours_left <= 24 else "warning"
            notif = await create_notification(
                user_id=student_id,
                type="deadline_reminder",
                severity=severity,
                title=f"{item['type']} due soon: {item['title']}",
                message=f"'{item['title']}' is due in less than {int(hours_left)} hours.",
                link=item.get("link"),
                course_id=course_id,
                dedupe_key=f"deadline:{item['title']}:{course_id}",
            )
            if notif:
                created.append(notif)

    return created