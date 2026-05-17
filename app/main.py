from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.database import engine, Base
from app.routes import auth, employee, manager, admin

# Create all DB tables if they don't exist
Base.metadata.create_all(bind=engine)

# Create the FastAPI app
app = FastAPI(title="AtomQuest Goal Portal")

# Serve static files (CSS, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Register all route files
app.include_router(auth.router)
app.include_router(employee.router, prefix="/employee")
app.include_router(manager.router, prefix="/manager")
app.include_router(admin.router, prefix="/admin")

@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/login")