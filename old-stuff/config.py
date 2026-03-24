"""Configuration helper: loads environment variables and exposes secrets.

This will attempt to load a local `.env` using `python-dotenv` if installed.
Use `API_KEY = config.API_KEY` in your code after importing this module.
"""
from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

if load_dotenv:
    # Safe to call even if .env is missing
    load_dotenv()

API_KEY = os.environ.get("API_KEY")
TOKEN = os.environ.get("TOKEN")

def get_api_key() -> str | None:
    """Return the configured API key or None if missing."""
    return API_KEY
