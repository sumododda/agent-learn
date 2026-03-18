from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://agentlearn:agentlearn@localhost:5432/agentlearn"
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "anthropic/claude-sonnet-4"

    model_config = {"env_file": ".env"}


settings = Settings()
