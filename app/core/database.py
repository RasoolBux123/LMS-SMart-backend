from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional
from .config import settings
class Database:
    client: Optional[AsyncIOMotorClient] = None
    db = None
    
    async def connect(self):
        self.client = AsyncIOMotorClient(settings.mongodb_uri)
        self.db = self.client.get_database()
        print("Connected to MongoDB")
        
    async def disconnect(self):
        if self.client:
            self.client.close()
            print("Disconnected from MongoDB")
            
    def get_db(self):
        return self.db

database = Database()