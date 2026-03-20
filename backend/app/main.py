from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.limiter import limiter
from app.routers import chat, courses, internal
from app.routers.auth_routes import router as auth_router

app = FastAPI(title="agent-learn")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(courses.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(internal.router, prefix="/api")
app.include_router(auth_router, prefix="/api/auth")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
