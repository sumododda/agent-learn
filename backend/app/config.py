from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://agentlearn:agentlearn@localhost:5432/agentlearn"
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "anthropic/claude-sonnet-4"
    TAVILY_API_KEY: str = ""
    INTERNAL_API_TOKEN: str = ""
    TRIGGER_SECRET_KEY: str = ""
    TRIGGER_API_URL: str = "https://api.trigger.dev"

    model_config = {"env_file": ".env"}


settings = Settings()
