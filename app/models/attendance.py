from datetime import datetime


def new_attendance_doc(
    course_id: str,
    student_id: str,
    date: str,
    status: str,
    marked_by: str,
):
    return {
        "courseId": course_id,
        "studentId": student_id,
        "date": date,
        "status": status,
        "markedBy": marked_by,
        "createdAt": datetime.utcnow(),
    }


def attendance_to_public(doc: dict):
    return {
        "id": str(doc["_id"]),
        "courseId": doc["courseId"],
        "studentId": doc["studentId"],
        "date": doc["date"],
        "status": doc["status"],
        "markedBy": doc["markedBy"],
        "createdAt": doc["createdAt"],
    }