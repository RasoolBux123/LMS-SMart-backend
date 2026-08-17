from datetime import datetime


def new_exam_doc(
    course_id: str,
    title: str,
    description: str,
    exam_file: str,
    total_marks: int,
    due_at,
    created_by: str,
):
    now = datetime.utcnow()

    return {
        "courseId": course_id,
        "title": title,
        "description": description,
        "examFile": exam_file,
        "totalMarks": total_marks,
        "dueAt": due_at,
        "createdBy": created_by,
        "createdAt": now,
        "updatedAt": now,
    }


def exam_to_public(doc):
    return {
        "id": str(doc["_id"]),
        "courseId": doc["courseId"],
        "title": doc["title"],
        "description": doc["description"],
        "examFile": doc["examFile"],
        "totalMarks": doc["totalMarks"],
        "dueAt": doc["dueAt"],
        "createdBy": doc["createdBy"],
        "createdAt": doc["createdAt"],
        "updatedAt": doc.get("updatedAt"),
    }