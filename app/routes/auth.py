from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from app.auth import verify_password, create_token, hash_password
from fastapi import HTTPException
import os

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


# Temporary route to create test users (we'll remove this later)
@router.get("/setup")
def setup(db: Session = Depends(get_db)):
    if os.getenv("ALLOW_SETUP_ROUTE", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Not found")

    created = 0

    manager = db.query(User).filter(User.email == "manager@test.com").first()
    if not manager:
        manager = User(
            name="Rahul Manager",
            email="manager@test.com",
            password=hash_password("password123"),
            role="manager",
        )
        db.add(manager)
        db.flush()
        created += 1

    admin_user = db.query(User).filter(User.email == "admin@test.com").first()
    if not admin_user:
        db.add(
            User(
                name="HR Admin",
                email="admin@test.com",
                password=hash_password("password123"),
                role="admin",
            )
        )
        created += 1

    employee = db.query(User).filter(User.email == "employee@test.com").first()
    if not employee:
        db.add(
            User(
                name="Ak Employee",
                email="employee@test.com",
                password=hash_password("password123"),
                role="employee",
                manager_id=manager.id,
            )
        )
        created += 1
    elif employee.manager_id != manager.id:
        employee.manager_id = manager.id

    db.commit()
    return {"message": "Setup completed", "created": created}