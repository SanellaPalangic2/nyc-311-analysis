"""Shared settings for every script in the project.

Reads secrets from the .env file so nothing sensitive is written in the code.
Every other script imports from here instead of reading .env on its own.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = the folder above src/. Building paths from this file's own
# location means the scripts work no matter which folder you run them from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
SQL_DIR = PROJECT_ROOT / "sql"
CHARTS_DIR = PROJECT_ROOT / "outputs" / "charts"
POWERBI_DIR = PROJECT_ROOT / "outputs" / "powerbi"

# Socrata API for "311 Service Requests from 2020 to Present"
API_URL = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"

# Analysis scope: requests created during calendar year 2025.
START_DATE = "2025-01-01T00:00:00"
END_DATE = "2026-01-01T00:00:00"  # exclusive upper bound

# Optional token. An empty string counts as "no token".
SOCRATA_APP_TOKEN = os.getenv("SOCRATA_APP_TOKEN") or None


def get_database_url():
    """Return the Postgres connection string, or fail with a clear message."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill it in."
        )
    return url


def get_engine():
    """SQLAlchemy engine, which is what pandas.read_sql expects.

    'postgresql+psycopg://' tells SQLAlchemy to use the psycopg 3 driver; plain
    'postgresql://' would make it look for the older psycopg2, which we don't
    install.
    """
    from sqlalchemy import create_engine

    url = get_database_url().replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(url)
