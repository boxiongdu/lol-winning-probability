"""Load local configuration without exposing credentials in logs."""
import os
import math
from pathlib import Path
from dotenv import load_dotenv
from src.errors import ConfigurationError

ROOT = Path(__file__).resolve().parents[1]


def load_environment():
    """Load the repository .env without overriding existing environment variables."""
    load_dotenv(ROOT / ".env")


def database_url():
    """Require a PostgreSQL connection string for database commands."""
    load_environment()
    value = os.getenv("DATABASE_URL", "")
    if not value.strip():
        raise ConfigurationError("DATABASE_URL is missing. Set it in the repository-root .env or environment.")
    return value


def env_number(name, default, kind=int):
    """Parse a numeric setting without echoing a possibly sensitive invalid value."""
    try:
        value = kind(os.getenv(name, str(default)))
        if not math.isfinite(value):
            raise ValueError
    except (ValueError, OverflowError):
        label = "integer" if kind is int else "finite number"
        raise ConfigurationError(f"{name} must be a valid {label}.") from None
    return value
