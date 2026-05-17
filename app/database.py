from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./atomquest.db")

# Create the engine - this is the actual connection ot SQLite file
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

# Each request gets its own session - like a transcation
Sessionlocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class - all your models will inherit from this
Base = declarative_base()

# Dependency - used in FastAPI to get a session for each request
def get_db():
    db = Sessionlocal()
    try:
        yield db
    finally:
        db.close()