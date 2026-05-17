from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from app.auth import require_admin

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

@router.get("/dashboard")
def dashboard(request: Request, user=Depends(require_admin)):
    return templates.TemplateResponse(request, "admin/dashboard.html", {"user": user})