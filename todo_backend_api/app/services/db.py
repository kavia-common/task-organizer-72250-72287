"""
MongoDB connection service for the Flask backend.

This module initializes a single MongoClient for the process and exposes helpers
to access the configured database. Configuration is read from environment
variables:
  - MONGODB_URL: MongoDB connection string (required)
  - MONGODB_DB:  Database name to use (required)

Usage:
    from app.services.db import init_db, get_db

    def create_app():
        app = Flask(__name__)
        init_db(app)  # Initialize once on app creation
        db = get_db() # Acquire database handle when needed
        return app
"""
from __future__ import annotations

import os
from typing import Optional

from flask import current_app
from pymongo import MongoClient
from pymongo.database import Database

# Module-level singletons
_client: Optional[MongoClient] = None
_db: Optional[Database] = None


def _create_mongo_client(mongo_url: str) -> MongoClient:
    """Create a MongoClient with sane defaults."""
    # Connect lazily; pymongo establishes connection on first operation.
    return MongoClient(mongo_url, tlsAllowInvalidCertificates=False)


# PUBLIC_INTERFACE
def init_db(app) -> None:
    """Initialize the MongoDB client and database for the Flask app.

    Reads MONGODB_URL and MONGODB_DB from the environment. Stores a reference
    in this module for global access and attaches a lightweight reference on
    the Flask app (app.extensions["mongo_db"]) for diagnostics.

    Raises:
        RuntimeError: If required environment variables are missing.
    """
    global _client, _db

    mongo_url = os.getenv("MONGODB_URL")
    db_name = os.getenv("MONGODB_DB")

    if not mongo_url:
        raise RuntimeError(
            "MONGODB_URL is not set. Please set it in the environment."
        )
    if not db_name:
        raise RuntimeError(
            "MONGODB_DB is not set. Please set it in the environment."
        )

    _client = _create_mongo_client(mongo_url)
    _db = _client[db_name]

    # Record in app extensions for visibility
    if not hasattr(app, "extensions"):
        app.extensions = {}
    app.extensions["mongo_db"] = {"db_name": db_name}

    # Ensure clean shutdown on app context teardown
    @app.teardown_appcontext
    def _close_mongo_client(exception):  # noqa: ANN001
        # Keep singleton for process lifetime; do not close here to avoid
        # closing between requests in some server modes. Left intentionally empty.
        return None


# PUBLIC_INTERFACE
def get_db() -> Database:
    """Return the configured MongoDB Database.

    This function requires init_db(app) to have been called during app startup.

    Returns:
        pymongo.database.Database: The configured database handle.

    Raises:
        RuntimeError: If the database has not been initialized.
    """
    if _db is None:
        # Provide a clearer error message in development.
        app_name = None
        try:
            app_name = current_app.name  # will fail outside app/app_context
        except Exception:
            pass
        raise RuntimeError(
            "MongoDB is not initialized. Call init_db(app) during app startup."
            + (f" (current_app={app_name})" if app_name else "")
        )
    return _db
