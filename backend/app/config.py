from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://agentlearn:agentlearn@localhost:5432/agentlearn"
    JWT_SECRET_KEY: str = ""
    JWT_EXPIRE_MINUTES: int = 1440
    ENCRYPTION_PEPPER: str = ""
    TURNSTILE_SECRET_KEY: str = ""
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = "onboarding@resend.dev"
    CORS_ORIGINS: str = "http://localhost:3000"
    DOCS_ENABLED: bool = False

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()


def validate_settings() -> None:
    """Validate critical settings on startup. Raises RuntimeError if invalid."""
    if not settings.JWT_SECRET_KEY or len(settings.JWT_SECRET_KEY) < 32:
        raise RuntimeError("JWT_SECRET_KEY must be at least 32 characters")
    if not settings.ENCRYPTION_PEPPER or len(settings.ENCRYPTION_PEPPER) < 32:
        raise RuntimeError("ENCRYPTION_PEPPER must be at least 32 characters")
    if not settings.TURNSTILE_SECRET_KEY:
        raise RuntimeError("TURNSTILE_SECRET_KEY must be set — refusing to start without Turnstile")
    if not settings.RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY must be set — refusing to start without email service")
