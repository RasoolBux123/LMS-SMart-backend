import asyncio
from app.core.database import database
from app.core.security import get_password_hash
from app.models.user import new_user_doc

async def seed():
    await database.connect()
    db = database.db
    existing = await db.users.find_one({"email": "admin@smartlms.com"})
    if existing:
        print("Admin already exists.")
        return
    doc = new_user_doc(
        name="Super Admin",
        email="admin@smartlms.com",
        password_hash=get_password_hash("ChangeMe123"),
        role="admin",
    )
    await db.users.insert_one(doc)
    print("Admin created: admin@smartlms.com / ChangeMe123")

asyncio.run(seed())