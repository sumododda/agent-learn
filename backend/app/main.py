import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

# Configure logging so INFO+ messages from our app are visible
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
# Silence noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("langchain").setLevel(logging.WARNING)
logging.getLogger("langsmith").setLevel(logging.WARNING)
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import settings, validate_settings
from app.limiter import limiter
from app.routers import academic_provider_routes, chat, courses, provider_routes, search_provider_routes
from app.routers.auth_routes import router as auth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_settings()
    yield


app = FastAPI(
    title="agent-learn",
    lifespan=lifespan,
    docs_url="/docs" if settings.DOCS_ENABLED else None,
    redoc_url="/redoc" if settings.DOCS_ENABLED else None,
    openapi_url="/openapi.json" if settings.DOCS_ENABLED else None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",")],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(courses.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(auth_router, prefix="/api/auth")
app.include_router(provider_routes.router, prefix="/api")
app.include_router(search_provider_routes.router, prefix="/api")
app.include_router(academic_provider_routes.router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
