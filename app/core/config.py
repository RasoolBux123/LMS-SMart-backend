from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    mongodb_uri: str
    secret_key: str
    backend_port: int = 8000
    frontend_url: str = "http://localhost:3000"
    environment: str = "development"
    # accept both ALGORITHM and JWT_ALGORITHM from .env
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
