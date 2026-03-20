from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import chat, courses, internal

app = FastAPI(title="agent-learn")

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


@app.get("/api/health")
async def health():
    return {"status": "ok"}
