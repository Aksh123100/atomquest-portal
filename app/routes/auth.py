from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from app.auth import verify_password, create_token

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "shared/login.html")


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    # Find user by email
    user = db.query(User).filter(User.email == email).first()

    # Check user exists and password is correct
    if not user or not verify_password(password, user.password):
        return templates.TemplateResponse(request, "shared/login.html", {
            "error": "Invalid email or password"
        })

    # Create JWT token with user info
    token = create_token({"user_id": user.id, "role": user.role})

    # Redirect based on role
    if user.role == "employee":
        redirect_url = "/employee/dashboard"
    elif user.role == "manager":
        redirect_url = "/manager/dashboard"
    else:
        redirect_url = "/admin/dashboard"

    # Set token in cookie and redirect
    response = RedirectResponse(url=redirect_url, status_code=302)
    response.set_cookie(key="access_token", value=token, httponly=True)
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("access_token")
    return response