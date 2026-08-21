# app/services/ai_insights.py

import json
import re
from datetime import datetime, timezone
from typing import Optional, List, Literal
from pydantic import BaseModel

from openai import AzureOpenAI

from app.core.config import (
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_API_KEY,
    MODEL_DEPLOYMENT,
    AZURE_OPENAI_API_VERSION,
)
from app.core.database import database


def get_ai_insights_collection():
    return database.get_db()["ai_insights"]


# ============================================================
# 0. Tuning knobs
# ============================================================
# An item below this absolute percentage is treated as weak even when the whole
# component is uniformly low (relative-outlier tests miss that case entirely).
WEAK_ITEM_FLOOR = 70.0

# Relative test: item is an outlier if it sits this far below its component average.
RELATIVE_OUTLIER_GAP = 15.0

MAX_SUGGESTED_TOPICS = 3
MAX_TOPIC_CHARS = 48


# ============================================================
# 1. Schemas
# ============================================================
ComponentType = Literal["Assignment", "Quiz", "Project", "Exam"]
SubmissionStatus = Literal["Submitted", "Pending", "Not Submitted"]
RiskCategory = Literal["top", "on_track", "at_risk", "failure_risk", "incomplete_data"]


class GradedItem(BaseModel):
    name: str
    total: float
    obtained: float
    status: SubmissionStatus
    remarks: Optional[str] = None
    description: Optional[str] = None


class ComponentSummary(BaseModel):
    type: ComponentType
    weightage: float  # e.g. 25.0 (%)
    items: List[GradedItem]


class AttendanceSummary(BaseModel):
    total_classes: int
    attended: int
    late: int = 0
    absent: int = 0
    # Optional: last N sessions for trend detection, e.g. ["Present","Present","Absent","Absent"]
    recent_trend: List[Literal["Present", "Absent", "Late"]] = []


class StudentGradeData(BaseModel):
    student_id: str
    student_name: str
    course_id: str
    course_name: str
    components: List[ComponentSummary]
    attendance: Optional[AttendanceSummary] = None


class ComponentBreakdown(BaseModel):
    type: ComponentType
    obtained_pct: float
    weighted_contribution: float
    loss_contribution: float


class MissingSubmission(BaseModel):
    component: ComponentType
    item: str
    description: Optional[str] = None


class OutlierItem(BaseModel):
    component: ComponentType
    item: str
    obtained_pct: float
    description: Optional[str] = None


class AttendanceFlag(BaseModel):
    attendance_pct: float
    status: Literal["good", "warning", "critical"]
    declining: bool  # true if recent_trend shows a drop-off


class RuleEngineResult(BaseModel):
    risk_category: RiskCategory
    overall_weighted_pct: float
    component_breakdown: List[ComponentBreakdown]
    weakest_component: Optional[ComponentType] = None
    missing_submissions: List[MissingSubmission] = []
    outlier_items: List[OutlierItem] = []
    graded_weightage: float = 100.0
    attendance_flag: Optional[AttendanceFlag] = None


class AIInsightOut(BaseModel):
    student_id: str
    course_id: str
    risk_category: RiskCategory
    instructor_insight: str
    student_message: str
    focus_topic: Optional[str] = None
    suggested_topics: List[str] = []
    attendance_pct: Optional[float] = None
    generated_at: str


class StudentInsightOut(BaseModel):
    student_message: str
    focus_topic: Optional[str] = None
    suggested_topics: List[str] = []
    attendance_pct: Optional[float] = None


# ============================================================
# 2. Azure OpenAI client
# ============================================================
azure_client = AzureOpenAI(
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_API_KEY,
    api_version=AZURE_OPENAI_API_VERSION,
)


# ============================================================
# 3. Rule engine — deterministic, no AI call
# ============================================================
def round2(n: float) -> float:
    return round(n, 2)


def evaluate_attendance(attendance: Optional[AttendanceSummary]) -> Optional[AttendanceFlag]:
    if not attendance or attendance.total_classes == 0:
        return None

    pct = round2((attendance.attended / attendance.total_classes) * 100)

    if pct >= 85:
        status = "good"
    elif pct >= 70:
        status = "warning"
    else:
        status = "critical"

    # declining = last 3 of recent_trend show 2+ absences
    declining = False
    if attendance.recent_trend:
        last_few = attendance.recent_trend[-3:]
        declining = last_few.count("Absent") >= 2

    return AttendanceFlag(attendance_pct=pct, status=status, declining=declining)


def classify_risk(
    pct: float,
    incomplete: bool,
    missing_count: int,
    attendance_flag: Optional[AttendanceFlag],
) -> str:
    if attendance_flag and attendance_flag.status == "critical":
        if pct < 60:
            return "failure_risk"
        return "at_risk"

    if incomplete and missing_count >= 2:
        return "incomplete_data"
    if pct >= 80 and (not attendance_flag or attendance_flag.status != "warning"):
        return "top"
    if pct >= 60:
        return "on_track"
    if pct >= 40:
        return "at_risk"
    return "failure_risk"


def run_rule_engine(data: StudentGradeData) -> RuleEngineResult:
    breakdown: List[ComponentBreakdown] = []
    missing_submissions: List[MissingSubmission] = []
    outlier_items: List[OutlierItem] = []
    overall_weighted_pct = 0.0
    graded_weightage = 0.0
    has_incomplete_data = False

    for comp in data.components:
        if not comp.items:
            continue

        graded_items = [i for i in comp.items if i.status != "Pending"]

        for item in comp.items:
            if item.status != "Submitted":
                missing_submissions.append(
                    MissingSubmission(
                        component=comp.type,
                        item=item.name,
                        description=item.description,
                    )
                )
                has_incomplete_data = True

        if not graded_items:
            continue

        graded_weightage += comp.weightage

        total_marks = sum(i.total for i in graded_items)
        obtained_marks = sum(i.obtained for i in graded_items)
        obtained_pct = (obtained_marks / total_marks * 100) if total_marks > 0 else 0.0
        weighted_contribution = (obtained_pct / 100) * comp.weightage
        loss_contribution = ((100 - obtained_pct) / 100) * comp.weightage

        overall_weighted_pct += weighted_contribution

        breakdown.append(ComponentBreakdown(
            type=comp.type,
            obtained_pct=round2(obtained_pct),
            weighted_contribution=round2(weighted_contribution),
            loss_contribution=round2(loss_contribution),
        ))

        # FIX 1 — absolute floor alongside the relative test.
        # A component where every item is weak has no relative outliers, so the
        # old test alone handed the prompt an empty list and the model correctly
        # returned no topics.
        for item in graded_items:
            item_pct = (item.obtained / item.total * 100) if item.total > 0 else 0.0
            if item.status != "Submitted":
                continue
            is_relative_outlier = item_pct < obtained_pct - RELATIVE_OUTLIER_GAP
            is_absolutely_weak = item_pct < WEAK_ITEM_FLOOR
            if is_relative_outlier or is_absolutely_weak:
                outlier_items.append(
                    OutlierItem(
                        component=comp.type,
                        item=item.name,
                        obtained_pct=round2(item_pct),
                        description=item.description,
                    )
                )

    normalized_pct = round2(overall_weighted_pct / graded_weightage * 100) if graded_weightage > 0 else 0.0

    weakest = max(breakdown, key=lambda b: b.loss_contribution) if breakdown else None
    attendance_flag = evaluate_attendance(data.attendance)
    risk_category = classify_risk(
        normalized_pct,
        has_incomplete_data,
        len(missing_submissions),
        attendance_flag,
    )

    return RuleEngineResult(
        risk_category=risk_category,
        overall_weighted_pct=normalized_pct,
        graded_weightage=round2(graded_weightage),
        component_breakdown=breakdown,
        weakest_component=weakest.type if weakest else None,
        missing_submissions=missing_submissions,
        outlier_items=outlier_items,
        attendance_flag=attendance_flag,
    )


# ============================================================
# 4. Topic helpers (deterministic backstop + sanitiser)
# ============================================================
def _clean_topic(text: str) -> str:
    """Strip a leading label prefix ('Quiz 1:', 'Assignment 2 —') and tidy whitespace."""
    t = " ".join((text or "").split())
    # "Quiz 1: React Hooks" / "Assignment 2 — Express REST API" -> keep what's after the label
    m = re.match(r"^(.{1,20}?)\s*[:—–]\s*(.+)$", t)
    if m:
        t = m.group(2)
    if len(t) > MAX_TOPIC_CHARS:
        t = t[:MAX_TOPIC_CHARS].rsplit(" ", 1)[0].rstrip(",;") + "…"
    return t


def _sanitise_topics(raw: object) -> List[str]:
    """Keep non-empty strings, dedupe, cap the count."""
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        topic = _clean_topic(entry)
        if not topic or topic.lower() in {t.lower() for t in out}:
            continue
        out.append(topic)
    return out[:MAX_SUGGESTED_TOPICS]


def _fallback_topics(rule: RuleEngineResult) -> List[str]:
    """FIX 5 — deterministic topics from item content/titles when the model returns none."""
    candidates: List[str] = []
    for o in list(rule.outlier_items) + list(rule.missing_submissions):
        source = (getattr(o, "description", None) or "").strip() or (o.item or "").strip()
        if not source:
            continue
        candidates.append(source)
    return _sanitise_topics(candidates)


# ============================================================
# 5. Prompt builder
# ============================================================
def build_insight_prompt(data: StudentGradeData, rule: RuleEngineResult) -> str:
    breakdown_lines = "\n".join(
        f"- {c.type}: {c.obtained_pct}% obtained, contributes {c.loss_contribution}% loss to overall score"
        for c in rule.component_breakdown
    )

    # The item title is itself usable content, so it's always shown.
    def _desc(o) -> str:
        d = (getattr(o, "description", None) or "").strip()
        return f" — content: {d}" if d else " — no description, use the title above"

    outlier_lines = "\n".join(
        f"- {o.item} ({o.component}): {o.obtained_pct}%{_desc(o)}" for o in rule.outlier_items
    ) or "none"
    missing_lines = "\n".join(
        f"- {m.item} ({m.component}){_desc(m)}" for m in rule.missing_submissions
    ) or "none"

    if rule.attendance_flag:
        af = rule.attendance_flag
        attendance_block = (
            f"Attendance: {af.attendance_pct}% ({af.status})"
            + (" — declining in recent sessions" if af.declining else "")
        )
    else:
        attendance_block = "Attendance: no data available"

    return f"""
Student: {data.student_name}
Course: {data.course_name}
Overall weighted score: {rule.overall_weighted_pct}%
Risk category (pre-computed, do not change): {rule.risk_category}
{attendance_block}

Component breakdown:
{breakdown_lines}

Weakest component: {rule.weakest_component or "none"}

Outlier low-scoring items (with their actual content where available):
{outlier_lines}

Missing/pending submissions (with their actual content where available):
{missing_lines}

Rules for suggestedTopics:
- Take topics only from the weak items listed above — their title and content, not
  the course name.
- Each topic is a concrete subject or skill the student should study, never a
  component label and never study/exam advice.
- Spread the topics across the different weak items rather than one item.
- Return at most {MAX_SUGGESTED_TOPICS}, each a short searchable phrase under
  {MAX_TOPIC_CHARS} characters. Return an empty list if the items say nothing usable.

Generate a JSON object ONLY, no preamble, no markdown fences, with this exact shape:
{{
  "instructorInsight": "1-2 sentences for the teacher, naming ALL the specific weak items driving the risk (e.g. Quiz 1, Quiz 2, Assignment 2) if there is more than one, AND attendance pattern if relevant, plain factual tone",
  "studentMessage": "2-3 sentences, second person, encouraging but honest, reference ALL the specific weak items (not just one) and attendance if it's a factor, never mention 'risk category' or clinical labels",
  "focusTopic": "the weak component name, or null if not applicable",
  "suggestedTopics": ["<concrete subject-matter topic>", "<concrete subject-matter topic>"]
}}
""".strip()


# ============================================================
# 6. Azure OpenAI call
# ============================================================
def call_azure_openai(prompt: str) -> dict:
    """FIX 4 — log the raw reply so an empty topics array can be told apart from a parse failure."""
    raw_text = ""
    try:
        response = azure_client.chat.completions.create(
            model=MODEL_DEPLOYMENT,
            messages=[
                {"role": "system", "content": "You are an academic advisor AI. Always reply with valid JSON only, no markdown."},
                {"role": "user", "content": prompt},
            ],
            # Raised from 400: the two prose fields plus the topics array were
            # truncating mid-JSON, which silently dropped suggestedTopics.
            max_completion_tokens=700,
        )
        raw_text = response.choices[0].message.content or "{}"
        print(f"[ai_insights] raw response: {raw_text!r}")

        cleaned = raw_text.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            print(f"[ai_insights] expected a JSON object, got {type(parsed).__name__}")
            return {}
        return parsed
    except json.JSONDecodeError as e:
        print(f"[ai_insights] JSON parse failed ({e}) — raw was: {raw_text!r}")
        return {}
    except Exception as e:
        print(f"[ai_insights] Azure OpenAI call failed: {e}")
        return {}


# ============================================================
# 7. Full pipeline: rule engine + AI + save to Mongo
# ============================================================
async def generate_and_save_insight(data: StudentGradeData) -> AIInsightOut:
    rule = run_rule_engine(data)
    prompt = build_insight_prompt(data, rule)
    parsed = call_azure_openai(prompt)

    if not parsed:
        fallback_attendance = (
            f" Attendance: {rule.attendance_flag.attendance_pct}%." if rule.attendance_flag else ""
        )
        parsed = {
            "instructorInsight": f"Weighted score {rule.overall_weighted_pct}%. Weakest area: {rule.weakest_component or 'N/A'}.{fallback_attendance}",
            "studentMessage": "Keep working steadily — check in with your instructor about your recent scores and attendance.",
            "focusTopic": None,
            "suggestedTopics": [],
        }

    topics = _sanitise_topics(parsed.get("suggestedTopics"))
    if not topics:
        topics = _fallback_topics(rule)
        if topics:
            print(f"[ai_insights] model returned no topics — using deterministic fallback: {topics}")

    insight = AIInsightOut(
        student_id=data.student_id,
        course_id=data.course_id,
        risk_category=rule.risk_category,
        instructor_insight=parsed.get("instructorInsight", ""),
        student_message=parsed.get("studentMessage", ""),
        focus_topic=parsed.get("focusTopic"),
        suggested_topics=topics,
        attendance_pct=rule.attendance_flag.attendance_pct if rule.attendance_flag else None,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    await get_ai_insights_collection().update_one(
        {"student_id": insight.student_id, "course_id": insight.course_id},
        {"$set": insight.model_dump()},
        upsert=True,
    )

    return insight


# ============================================================
# 8. Fetch helpers
# ============================================================
async def get_insight(student_id: str, course_id: str) -> Optional[dict]:
    return await get_ai_insights_collection().find_one(
        {"student_id": student_id, "course_id": course_id}, {"_id": 0}
    )


async def get_all_insights_for_course(course_id: str) -> List[dict]:
    cursor = get_ai_insights_collection().find({"course_id": course_id}, {"_id": 0})
    return [doc async for doc in cursor]