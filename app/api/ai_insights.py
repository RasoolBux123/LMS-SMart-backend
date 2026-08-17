# app/api/ai_insights.py
from fastapi import APIRouter, HTTPException
from app.services.ai_insights import (
    StudentGradeData,
    generate_and_save_insight,
    get_insight,
    get_all_insights_for_course,
)

router = APIRouter(prefix="/api/ai-insights", tags=["ai-insights"])


@router.post("/generate")
async def generate_insight(data: StudentGradeData):
    """Call this whenever a grade or attendance record is saved."""
    insight = await generate_and_save_insight(data)
    return {"success": True, "insight": insight}


@router.get("/instructor/{student_id}")
async def instructor_view(student_id: str, course_id: str):
    insight = await get_insight(student_id, course_id)
    if not insight:
        raise HTTPException(status_code=404, detail="No insight found for this student/course")
    return insight


@router.get("/admin")
async def admin_view(course_id: str):
    insights = await get_all_insights_for_course(course_id)

    keys = ["top", "on_track", "at_risk", "failure_risk", "incomplete_data"]
    counts = {k: 0 for k in keys}
    for i in insights:
        counts[i["risk_category"]] += 1

    total = len(insights)
    percentages = {k: round((v / total) * 100, 2) if total else 0 for k, v in counts.items()}

    return {
        "course_id": course_id,
        "total_students": total,
        "risk_counts": counts,
        "risk_percentages": percentages,
    }


@router.get("/student")
async def student_view(
    course_id: str,
    student_id: str,  # TEMP: replace with JWT-derived current user id, see note below
):
    insight = await get_insight(student_id, course_id)
    if not insight:
        return None

    # strip instructor-only fields before returning to student
    return {
        "student_message": insight["student_message"],
        "focus_topic": insight["focus_topic"],
        "suggested_topics": insight.get("suggested_topics", []),
        "attendance_pct": insight.get("attendance_pct"),
    }

@router.get("/course/{course_id}/list")
async def course_insights_list(course_id: str):
    """Returns all per-student AI insights for a course, sorted by risk severity."""
    insights = await get_all_insights_for_course(course_id)

    risk_order = {"failure_risk": 0, "at_risk": 1, "incomplete_data": 2, "on_track": 3, "top": 4}
    insights.sort(key=lambda i: risk_order.get(i["risk_category"], 5))

    return insights