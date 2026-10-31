from datetime import datetime, timezone
from fastapi import APIRouter, Response, Cookie, HTTPException
from pydantic import BaseModel, EmailStr, Field
from ..auth import hash_password, verify_password, create_session, set_session_cookie, clear_session_cookie, get_user_from_token, COOKIE_NAME
from ..database import database_connection

router = APIRouter(prefix="/api/auth", tags=["auth"])

class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    confirm_password: str = Field(min_length=6, max_length=128)

class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

@router.post("/register", status_code=201)
def register(data: RegisterIn, response: Response):
    name = data.name.strip()
    email = data.email.lower().strip()
    if data.password != data.confirm_password:
        raise HTTPException(400, "Passwords do not match")
    with database_connection() as db:
        if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "An account with this email already exists")
        cur = db.execute(
            "INSERT INTO users(name,email,password_hash,created_at) VALUES(?,?,?,?)",
            (name, email, hash_password(data.password), datetime.now(timezone.utc).isoformat()),
        )
        user_id = cur.lastrowid
    token = create_session(user_id)
    set_session_cookie(response, token)
    return {"message": "Registration successful", "user": {"id": user_id, "name": name, "email": email}}

@router.post("/login")
def login(data: LoginIn, response: Response):
    email = data.email.lower().strip()
    with database_connection() as db:
        row = db.execute("SELECT id,name,email,password_hash FROM users WHERE email=?", (email,)).fetchone()
    if not row or not verify_password(data.password, row["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    token = create_session(row["id"])
    set_session_cookie(response, token)
    return {"message": "Login successful", "user": {"id": row["id"], "name": row["name"], "email": row["email"]}}

@router.post("/logout")
def logout(response: Response, traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    if traffic_session:
        with database_connection() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (traffic_session,))
    clear_session_cookie(response)
    return {"message": "Logged out"}

@router.get("/me")
def me(traffic_session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
    user = get_user_from_token(traffic_session)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {"user": user}
