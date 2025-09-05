from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from flask import request
from flask.views import MethodView
from flask_smorest import Blueprint
from marshmallow import Schema, fields, validate, validates_schema, ValidationError
from werkzeug.security import check_password_hash, generate_password_hash
from bson import ObjectId

from app.services.db import get_db

blp = Blueprint(
    "Auth",
    "auth",
    url_prefix="/auth",
    description="Authentication endpoints for user registration and login",
)


# ============
# Schemas
# ============
class UserSchema(Schema):
    """Public representation of a user object."""

    id = fields.String(dump_only=True, description="User ID")
    email = fields.Email(required=True, description="User email")
    name = fields.String(required=False, allow_none=True, description="User display name")
    created_at = fields.String(dump_only=True, description="ISO timestamp when created")
    updated_at = fields.String(dump_only=True, description="ISO timestamp when last updated")


class RegisterSchema(Schema):
    email = fields.Email(required=True, description="Email for new account")
    password = fields.String(
        required=True,
        load_only=True,
        validate=validate.Length(min=8, error="Password must be at least 8 characters."),
        description="Plain text password (min 8 chars)",
    )
    name = fields.String(required=False, allow_none=True, description="Optional display name")

    @validates_schema
    def _strip_email(self, data, **kwargs):  # noqa: ANN001
        if "email" in data and isinstance(data["email"], str):
            data["email"] = data["email"].strip().lower()


class LoginSchema(Schema):
    email = fields.Email(required=True, description="Registered email")
    password = fields.String(required=True, load_only=True, description="Account password")

    @validates_schema
    def _strip_email(self, data, **kwargs):  # noqa: ANN001
        if "email" in data and isinstance(data["email"], str):
            data["email"] = data["email"].strip().lower()


class AuthResponseSchema(Schema):
    token = fields.String(description="JWT access token")
    user = fields.Nested(UserSchema, description="Authenticated user info")


# ============
# Helpers
# ============
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _get_jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET is not set. Please set it in the environment.")
    return secret


def _get_jwt_exp_delta() -> timedelta:
    # Optional: JWT_EXPIRES_HOURS, defaults to 24 hours
    hours_str = os.getenv("JWT_EXPIRES_HOURS", "24")
    try:
        hours = int(hours_str)
    except ValueError as exc:  # pragma: no cover - defensive
        raise RuntimeError("JWT_EXPIRES_HOURS must be an integer.") from exc
    return timedelta(hours=hours)


def _create_jwt_token(user_id: str, email: str) -> str:
    """Create a signed JWT token for the user."""
    secret = _get_jwt_secret()
    exp = datetime.now(timezone.utc) + _get_jwt_exp_delta()
    payload = {
        "sub": user_id,
        "email": email,
        "iat": datetime.now(timezone.utc),
        "exp": exp,
        "type": "access",
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    # PyJWT returns str in >= 2.x
    return token


def _decode_jwt_from_request() -> Dict[str, Any]:
    """Extract and decode JWT from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ValidationError("Missing or invalid Authorization header.")
    token = auth_header.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise ValidationError("Token has expired.")
    except jwt.InvalidTokenError:
        raise ValidationError("Invalid token.")


def _serialize_user(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("_id")),
        "email": doc.get("email"),
        "name": doc.get("name"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


# ============
# Routes
# ============

@blp.route("/register")
class Register(MethodView):
    """Create a new user account."""

    # PUBLIC_INTERFACE
    @blp.arguments(RegisterSchema, as_kwargs=True)
    @blp.response(201, UserSchema)
    def post(self, email: str, password: str, name: Optional[str] = None):
        """Register a new user.

        Request body:
            - email: Email for the new account
            - password: Plain text password (min 8 characters)
            - name: Optional display name

        Returns:
            - 201 Created with the created user (without password)
        """
        db = get_db()
        users = db["users"]

        # Check duplicates
        existing = users.find_one({"email": email})
        if existing:
            raise ValidationError({"email": ["Email is already registered."]})

        password_hash = generate_password_hash(password)
        now = _now_iso()
        user_doc = {
            "email": email,
            "password_hash": password_hash,
            "name": name,
            "created_at": now,
            "updated_at": now,
        }
        insert_result = users.insert_one(user_doc)
        user_doc["_id"] = insert_result.inserted_id

        return _serialize_user(user_doc)


@blp.route("/login")
class Login(MethodView):
    """Authenticate and get a JWT."""

    # PUBLIC_INTERFACE
    @blp.arguments(LoginSchema, as_kwargs=True)
    @blp.response(200, AuthResponseSchema)
    def post(self, email: str, password: str):
        """Login and receive a JWT token and user info.

        Request body:
            - email: Registered email
            - password: Account password

        Returns:
            - 200 OK with { token, user }
        """
        db = get_db()
        users = db["users"]

        doc = users.find_one({"email": email})
        if not doc or not check_password_hash(doc.get("password_hash", ""), password):
            # Generic error to avoid user enumeration
            raise ValidationError({"email": ["Invalid email or password."]})

        user = _serialize_user(doc)
        token = _create_jwt_token(user_id=user["id"], email=user["email"])
        return {"token": token, "user": user}


@blp.route("/me")
class Me(MethodView):
    """Return the current authenticated user."""

    # PUBLIC_INTERFACE
    @blp.response(200, UserSchema)
    def get(self):
        """Get current user information.

        Security:
            Requires Authorization: Bearer <token> header with a valid JWT.

        Returns:
            - 200 OK with the current user info.
        """
        payload = _decode_jwt_from_request()
        user_id = payload.get("sub")
        if not user_id:
            raise ValidationError("Invalid token payload.")

        db = get_db()
        users = db["users"]

        try:
            oid = ObjectId(user_id)
        except Exception:  # pragma: no cover - defensive parsing
            raise ValidationError("Invalid user id in token.")

        doc = users.find_one({"_id": oid})
        if not doc:
            raise ValidationError("User not found.")
        return _serialize_user(doc)
