from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://agentlearn:agentlearn@localhost:5432/agentlearn"
    JWT_SECRET_KEY: str = ""
    JWT_EXPIRE_MINUTES: int = 1440
    ENCRYPTION_PEPPER: str = ""
    TURNSTILE_SECRET_KEY: str = ""
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = "onboarding@resend.dev"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
