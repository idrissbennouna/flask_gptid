"""Configuration centrale de l'application (lue depuis les variables d'environnement / .env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)

# --- Base de données SQLite -------------------------------------------------
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", INSTANCE_DIR / "flaskgpt.db"))

# --- Fournisseur LLM (API compatible OpenAI : Groq par défaut) ---------------
LLM_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-20b")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))

# --- Limites ------------------------------------------------------------------
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "20"))
MAX_PROMPT_CHARS = 8000
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
