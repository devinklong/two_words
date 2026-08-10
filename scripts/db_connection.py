# Shared Postgres connection helper. Reads config/.env if present (via
# python-dotenv, optional), else falls back to os.environ / these defaults.
import os

import psycopg2

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed -- falls through to plain os.environ / defaults


def get_connection():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", 5432)),
        dbname=os.environ.get("DB_NAME", "postgres"),
        user=os.environ.get("DB_USER", "devinlong"),
    )
