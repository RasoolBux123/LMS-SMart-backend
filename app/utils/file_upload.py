import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, List
import aiofiles
from fastapi import UploadFile, HTTPException

# Configuration
BASE_DIR = Path(__file__).parent.parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"

ALLOWED_EXTENSIONS = {
    "image": [".jpg", ".jpeg", ".png", ".gif", ".webp"],
    "document": [".pdf", ".doc", ".docx", ".txt", ".rtf"],
    "video": [".mp4", ".avi", ".mov", ".mkv", ".webm"],
    "audio": [".mp3", ".wav", ".aac", ".ogg"],
    "archive": [".zip", ".rar", ".7z", ".tar", ".gz"],
    "presentation": [".ppt", ".pptx", ".key"],
    "spreadsheet": [".xls", ".xlsx", ".csv"],
}

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB


def create_upload_dirs():
    """Create all necessary upload directories"""
    dirs = [
        UPLOAD_DIR / "assignments",
        UPLOAD_DIR / "submissions",
        UPLOAD_DIR / "materials",
        UPLOAD_DIR / "profiles",
        UPLOAD_DIR / "temp",
    ]
    for dir_path in dirs:
        dir_path.mkdir(parents=True, exist_ok=True)
    print("✅ Upload directories ready")


def get_file_extension(filename: str) -> str:
    """Get file extension from filename"""
    return Path(filename).suffix.lower()


def validate_file(
    file: UploadFile, allowed_types: List[str] = None, max_size: int = MAX_FILE_SIZE
):
    """Validate uploaded file"""
    # Check file size
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)

    if size > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Max size: {max_size // (1024*1024)} MB",
        )

    # Check extension
    if allowed_types:
        extension = get_file_extension(file.filename)
        if extension not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"File type not allowed. Allowed: {', '.join(allowed_types)}",
            )

    return True


def generate_filename(original_name: str, prefix: str = "") -> str:
    """Generate unique filename"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    extension = get_file_extension(original_name)
    clean_name = Path(original_name).stem
    # Remove special characters
    clean_name = "".join(c for c in clean_name if c.isalnum() or c in " _-")
    clean_name = clean_name.replace(" ", "_")

    if prefix:
        return f"{prefix}_{timestamp}_{clean_name}{extension}"
    return f"{timestamp}_{clean_name}{extension}"


async def save_upload_file(
    file: UploadFile,
    subfolder: str = "",
    prefix: str = "",
    allowed_types: List[str] = None,
) -> dict:
    """
    Save uploaded file and return file info

    Returns:
        dict: {
            "filename": "saved_filename",
            "original_name": "original_filename",
            "path": "relative/path/to/file",
            "size": file_size,
            "extension": ".pdf",
            "url": "/uploads/subfolder/filename"
        }
    """
    # Ensure upload directory exists
    upload_dir = UPLOAD_DIR / subfolder
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Validate file
    validate_file(file, allowed_types)

    # Generate unique filename
    filename = generate_filename(file.filename, prefix)
    file_path = upload_dir / filename

    # Save file
    try:
        async with aiofiles.open(file_path, "wb") as out_file:
            content = await file.read()
            await out_file.write(content)

        # Get file size
        file_size = os.path.getsize(file_path)

        return {
            "filename": filename,
            "original_name": file.filename,
            "path": str(file_path.relative_to(UPLOAD_DIR)),
            "size": file_size,
            "extension": get_file_extension(file.filename),
            "url": (
                f"/uploads/{subfolder}/{filename}"
                if subfolder
                else f"/uploads/{filename}"
            ),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")


async def delete_file(file_path: str):
    """Delete a file"""
    try:
        full_path = UPLOAD_DIR / file_path
        if full_path.exists():
            os.remove(full_path)
            return True
        return False
    except Exception as e:
        print(f"Error deleting file: {e}")
        return False
