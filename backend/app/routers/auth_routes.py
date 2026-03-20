"""Register and login endpoints for local JWT auth."""
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.auth import create_access_token, pwd_context
from app.database import SessionDep
from app.models import User
from app.schemas import AuthResponse, LoginRequest, RegisterRequest

router = APIRouter(tags=["auth"])


@router.post("/register", response_model=AuthResponse)
async def register(body: RegisterRequest, session: SessionDep):
    # Check for existing user with this email
    existing = await session.execute(
        select(User).where(User.email == body.email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=body.email,
        password_hash=pwd_context.hash(body.password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    token = create_access_token(str(user.id))
    return AuthResponse(token=token, user_id=str(user.id))


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, session: SessionDep):
    result = await session.execute(
        select(User).where(User.email == body.email)
    )
    user = result.scalar_one_or_none()
    if not user or not pwd_context.verify(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(str(user.id))
    return AuthResponse(token=token, user_id=str(user.id))
