"""Load local configuration without exposing credentials in logs."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def load_environment():
    """Load the repository .env without overriding existing environment variables."""
    load_dotenv(ROOT / ".env")


def database_url():
    """Require a PostgreSQL connection string for database commands."""
    load_environment()
    value = os.getenv("DATABASE_URL", "")
    if not value:
        raise ValueError("Set DATABASE_URL in the environment or local .env")
    return value
