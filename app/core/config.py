import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- load .env FIRST, before reading anything ---
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # goes up from app/core/ to backend/
load_dotenv(dotenv_path=BASE_DIR / ".env")

# --- Azure OpenAI (AI Insights) ---
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
MODEL_DEPLOYMENT = os.getenv("MODEL_DEPLOYMENT", "gpt-4o")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")


class Settings(BaseSettings):
    mongodb_uri: str
    secret_key: str
    backend_port: int = 8000
    frontend_url: str = "http://localhost:3000"
    environment: str = "development"
    jwt_algorithm: str = "HS256"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 10080

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def effective_algorithm(self) -> str:
        return self.jwt_algorithm or self.algorithm or "HS256"


settings = Settings()