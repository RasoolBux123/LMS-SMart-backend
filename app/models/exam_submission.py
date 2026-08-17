from datetime import datetime


def new_exam_submission_doc(
    exam_id: str,
    student_id: str,
    answer_file: str,
):
    now = datetime.utcnow()

    return {
        "examId": exam_id,
        "studentId": student_id,
        "answerFile": answer_file,
        "score": None,
        "feedback": "",
        "gradedBy": None,
        "submittedAt": now,
        "gradedAt": None,
    }


def exam_submission_to_public(doc):
    return {
        "id": str(doc["_id"]),
        "examId": doc["examId"],
        "studentId": doc["studentId"],
        "answerFile": doc["answerFile"],
        "score": doc.get("score"),
        "feedback": doc.get("feedback"),
        "gradedBy": doc.get("gradedBy"),
        "submittedAt": doc["submittedAt"],
        "gradedAt": doc.get("gradedAt"),
    }