import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Load environment variables dari root
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

# Ambil DATABASE_URL dari .env
DATABASE_URL = os.getenv("DATABASE_URL")

# Buat engine & session
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def insert_message(user_id, message):
    """Menyimpan pesan pengguna ke database."""
    db = SessionLocal()
    try:
        db.execute(
            text("INSERT INTO messages (user_id, message) VALUES (:user_id, :message)"),
            {"user_id": user_id, "message": message}
        )
        db.commit()
    finally:
        db.close()
