from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://agentlearn:agentlearn@localhost:5432/agentlearn"
    JWT_SECRET_KEY: str = ""
    JWT_EXPIRE_MINUTES: int = 1440
    ENCRYPTION_PEPPER: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
