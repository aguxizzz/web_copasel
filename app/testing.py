import os
from pathlib import Path
from dotenv import load_dotenv
BASE_DIR = Path(__file__).resolve().parent.parent
env_path = BASE_DIR / ".gitignore" / ".env"
env_path = Path(__file__).parent.parent / ".gitignore" /'.env'
print(env_path)
load_dotenv(env_path)

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
print(SECRET_KEY)