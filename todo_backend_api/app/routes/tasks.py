from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from flask import request
from flask.views import MethodView
from flask_smorest import Blueprint
from marshmallow import Schema, fields, validate, validates_schema, ValidationError

from app.services.db import get_db
from .auth import _decode_jwt_from_request, _now_iso  # reuse auth helpers

blp = Blueprint(
    "Tasks",
    "tasks",
    url_prefix="/tasks",
    description="CRUD endpoints for tasks and nested subtasks with JWT authentication",
)


# ============
# Schemas
# ============
class PaginationSchema(Schema):
    total = fields.Integer(description="Total number of items")
    total_pages = fields.Integer(description="Total number of pages")
    first_page = fields.Integer(description="First page index (1-based)")
    last_page = fields.Integer(description="Last page index (1-based)")
    page = fields.Integer(description="Current page (1-based)")
    previous_page = fields.Integer(allow_none=True, description="Previous page if available")
    next_page = fields.Integer(allow_none=True, description="Next page if available")


class TaskBaseSchema(Schema):
    title = fields.String(required=True, validate=validate.Length(min=1), description="Task title")
    description = fields.String(allow_none=True, description="Detailed description")
    priority = fields.Integer(
        allow_none=True,
        validate=validate.Range(min=1, max=5, error="Priority must be between 1 and 5"),
        description="Priority (1 highest - 5 lowest)",
    )
    estimate_minutes = fields.Integer(
        allow_none=True,
        validate=validate.Range(min=0),
        description="Estimated time in minutes",
    )
    due_date = fields.String(
        allow_none=True,
        description="Due date as ISO 8601 string (e.g., 2024-12-31T23:59:59Z)",
    )
    parent_id = fields.String(
        allow_none=True,
        description="Optional parent task id to create a subtask",
    )
    completed = fields.Boolean(
        missing=False,
        description="Completion status (defaults to false on create)",
    )


class TaskUpdateSchema(Schema):
    title = fields.String(validate=validate.Length(min=1), description="Task title")
    description = fields.String(allow_none=True, description="Detailed description")
    priority = fields.Integer(
        allow_none=True,
        validate=validate.Range(min=1, max=5, error="Priority must be between 1 and 5"),
        description="Priority (1 highest - 5 lowest)",
    )
    estimate_minutes = fields.Integer(
        allow_none=True,
        validate=validate.Range(min=0),
        description="Estimated time in minutes",
    )
    due_date = fields.String(
        allow_none=True,
        description="Due date as ISO 8601 string (e.g., 2024-12-31T23:59:59Z)",
    )
    parent_id = fields.String(
        allow_none=True,
        description="Set/Change parent id (null to make it a root task)",
    )
    completed = fields.Boolean(description="Completion status")

    @validates_schema
    def at_least_one(self, data, **kwargs):  # noqa: ANN001
        if not data:
            raise ValidationError("At least one field must be provided to update.")


class TaskQuerySchema(Schema):
    q = fields.String(
        required=False,
        description="Search query for title/description (case-insensitive substring)",
    )
    completed = fields.Boolean(required=False, description="Filter by completion")
    priority = fields.Integer(
        required=False, validate=validate.Range(min=1, max=5), description="Filter by priority"
    )
    due_before = fields.String(required=False, description="ISO timestamp; due_date <= this")
    due_after = fields.String(required=False, description="ISO timestamp; due_date >= this")
    parent_id = fields.String(
        required=False,
        allow_none=True,
        description="Filter by parent id; use 'null' or empty to get root-level tasks",
    )
    sort_by = fields.String(
        required=False,
        validate=validate.OneOf(
            ["created_at", "updated_at", "due_date", "priority", "estimate_minutes", "title"]
        ),
        description="Sort field",
        missing="created_at",
    )
    sort_order = fields.String(
        required=False,
        validate=validate.OneOf(["asc", "desc"]),
        description="Sort order",
        missing="desc",
    )
    page = fields.Integer(
        required=False, validate=validate.Range(min=1), description="Page number (1-based)", missing=1
    )
    page_size = fields.Integer(
        required=False,
        validate=validate.Range(min=1, max=100),
        description="Items per page (default 20, max 100)",
        missing=20,
    )


class TaskSchema(Schema):
    """Public representation of a task object."""

    id = fields.String(dump_only=True, description="Task ID")
    user_id = fields.String(dump_only=True, description="Owner user id")
    title = fields.String(required=True, description="Task title")
    description = fields.String(allow_none=True, description="Detailed description")
    priority = fields.Integer(allow_none=True, description="Priority (1-5)")
    estimate_minutes = fields.Integer(allow_none=True, description="Estimated time in minutes")
    due_date = fields.String(allow_none=True, description="Due date ISO 8601")
    parent_id = fields.String(allow_none=True, description="Parent task id if subtask")
    completed = fields.Boolean(description="Completion status")
    created_at = fields.String(dump_only=True, description="Created ISO timestamp")
    updated_at = fields.String(dump_only=True, description="Updated ISO timestamp")
    completed_at = fields.String(allow_none=True, description="When task was completed (ISO)")
    subtasks = fields.List(fields.Nested(lambda: TaskSchema(exclude=("subtasks",))), dump_only=True)


class TaskListResponseSchema(Schema):
    items = fields.List(fields.Nested(TaskSchema), description="List of tasks")
    meta = fields.Nested(PaginationSchema, description="Pagination metadata")


# ============
# Helpers
# ============
def _require_user() -> str:
    """Return current user id from JWT or raise ValidationError."""
    payload = _decode_jwt_from_request()
    uid = payload.get("sub")
    if not uid:
        raise ValidationError("Invalid token payload.")
    return uid


def _oid(obj_id: Optional[str]) -> Optional[ObjectId]:
    if obj_id is None:
        return None
    s = obj_id.strip()
    if s in ("", "null", "None"):
        return None
    try:
        return ObjectId(s)
    except Exception as exc:
        raise ValidationError("Invalid id format.") from exc


def _serialize_task(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc.get("_id")),
        "user_id": str(doc.get("user_id")) if doc.get("user_id") else None,
        "title": doc.get("title"),
        "description": doc.get("description"),
        "priority": doc.get("priority"),
        "estimate_minutes": doc.get("estimate_minutes"),
        "due_date": doc.get("due_date"),
        "parent_id": str(doc.get("parent_id")) if doc.get("parent_id") else None,
        "completed": bool(doc.get("completed", False)),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "completed_at": doc.get("completed_at"),
    }


def _parse_iso(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        # Accept 'Z' form as UTC
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1] + "+00:00"
        return datetime.fromisoformat(dt_str)
    except Exception as exc:
        raise ValidationError("Invalid ISO datetime format.") from exc


def _build_subtree(user_id: str, root_id: ObjectId) -> Dict[str, Any]:
    """Fetch a task and recursively attach subtasks for the given user."""
    db = get_db()
    tasks = db["tasks"]

    root = tasks.find_one({"_id": root_id, "user_id": ObjectId(user_id)})
    if not root:
        raise ValidationError("Task not found.")

    def fetch_children(parent_oid: ObjectId) -> List[Dict[str, Any]]:
        children = list(tasks.find({"parent_id": parent_oid, "user_id": ObjectId(user_id)}))
        serialized: List[Dict[str, Any]] = []
        for ch in children:
            node = _serialize_task(ch)
            node["subtasks"] = fetch_children(ch["_id"])
            serialized.append(node)
        return serialized

    root_ser = _serialize_task(root)
    root_ser["subtasks"] = fetch_children(root["_id"])
    return root_ser


def _collect_descendants(user_id: str, root_oid: ObjectId) -> List[ObjectId]:
    """Return all descendant ObjectIds (including root) for cascading operations."""
    db = get_db()
    tasks = db["tasks"]
    collected = [root_oid]
    frontier = [root_oid]
    while frontier:
        cur = frontier.pop()
        children = list(tasks.find({"parent_id": cur, "user_id": ObjectId(user_id)}, {"_id": 1}))
        child_ids = [c["_id"] for c in children]
        collected.extend(child_ids)
        frontier.extend(child_ids)
    return collected


def _has_children(user_id: str, oid: ObjectId) -> bool:
    db = get_db()
    tasks = db["tasks"]
    return tasks.count_documents({"parent_id": oid, "user_id": ObjectId(user_id)}, limit=1) > 0


def _prevent_circular_parent(user_id: str, task_oid: ObjectId, new_parent_oid: Optional[ObjectId]) -> None:
    """Ensure new_parent_oid is not the task itself nor a descendant."""
    if new_parent_oid is None:
        return
    if task_oid == new_parent_oid:
        raise ValidationError("parent_id cannot be the task itself.")
    descendants = set(_collect_descendants(user_id, task_oid))
    if new_parent_oid in descendants:
        raise ValidationError("parent_id cannot be a descendant of the task (circular hierarchy).")


def _make_sort(sort_by: str, sort_order: str) -> List[Tuple[str, int]]:
    order = -1 if sort_order == "desc" else 1
    # Mapping to Mongo fields
    field_map = {
        "created_at": "created_at",
        "updated_at": "updated_at",
        "due_date": "due_date",
        "priority": "priority",
        "estimate_minutes": "estimate_minutes",
        "title": "title",
    }
    sort_field = field_map.get(sort_by, "created_at")
    return [(sort_field, order), ("_id", order)]


# ============
# Routes
# ============

@blp.route("")
class TasksCollection(MethodView):
    """List tasks with search/filter/sort/pagination and create new tasks."""

    # PUBLIC_INTERFACE
    @blp.arguments(TaskQuerySchema, location="query", as_kwargs=True)
    @blp.response(200, TaskListResponseSchema)
    def get(
        self,
        q: Optional[str] = None,
        completed: Optional[bool] = None,
        priority: Optional[int] = None,
        due_before: Optional[str] = None,
        due_after: Optional[str] = None,
        parent_id: Optional[str] = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        page_size: int = 20,
    ):
        """Get tasks for the current user.

        Query parameters:
          - q: Search text across title and description (case-insensitive).
          - completed: Filter by completion status.
          - priority: Filter by priority (1-5).
          - due_before: ISO date-time; tasks with due_date <= this value.
          - due_after: ISO date-time; tasks with due_date >= this value.
          - parent_id: Filter by parent; use 'null' or omit to get root-level tasks.
          - sort_by: created_at|updated_at|due_date|priority|estimate_minutes|title
          - sort_order: asc|desc
          - page: Page number (1-based).
          - page_size: Items per page (1-100).

        Security:
          Requires Authorization: Bearer <token>
        """
        user_id = _require_user()
        db = get_db()
        tasks = db["tasks"]

        # Build filter
        flt: Dict[str, Any] = {"user_id": ObjectId(user_id)}

        if q:
            safe_q = re.escape(q.strip())
            regex = {"$regex": safe_q, "$options": "i"}
            flt["$or"] = [{"title": regex}, {"description": regex}]

        if completed is not None:
            flt["completed"] = bool(completed)

        if priority is not None:
            flt["priority"] = priority

        if due_before:
            dt = _parse_iso(due_before)
            flt.setdefault("due_date", {})
            flt["due_date"]["$lte"] = dt.isoformat().replace("+00:00", "Z") if dt else None

        if due_after:
            dt = _parse_iso(due_after)
            flt.setdefault("due_date", {})
            flt["due_date"]["$gte"] = dt.isoformat().replace("+00:00", "Z") if dt else None

        if parent_id is not None:
            parent_oid = _oid(parent_id)
            flt["parent_id"] = parent_oid
        else:
            # Default to root-level tasks if parent_id omitted
            flt["parent_id"] = None

        sort_spec = _make_sort(sort_by, sort_order)

        total = tasks.count_documents(flt)
        last_page = (total + page_size - 1) // page_size if page_size > 0 else 1
        page = max(1, min(page, max(1, last_page)))

        cursor = (
            tasks.find(flt)
            .sort(sort_spec)
            .skip((page - 1) * page_size)
            .limit(page_size)
        )

        items = [_serialize_task(doc) for doc in cursor]
        meta = {
            "total": total,
            "total_pages": last_page,
            "first_page": 1,
            "last_page": last_page or 1,
            "page": page,
            "previous_page": page - 1 if page > 1 else None,
            "next_page": page + 1 if page < (last_page or 1) else None,
        }
        return {"items": items, "meta": meta}

    # PUBLIC_INTERFACE
    @blp.arguments(TaskBaseSchema, as_kwargs=True)
    @blp.response(201, TaskSchema)
    def post(
        self,
        title: str,
        description: Optional[str] = None,
        priority: Optional[int] = None,
        estimate_minutes: Optional[int] = None,
        due_date: Optional[str] = None,
        parent_id: Optional[str] = None,
        completed: bool = False,
    ):
        """Create a new task for the current user.

        Request body:
          - title: Task title (required)
          - description: Optional description
          - priority: Optional 1-5
          - estimate_minutes: Optional non-negative integer
          - due_date: Optional ISO 8601 datetime string
          - parent_id: Optional parent task id to create a subtask
          - completed: Optional initial completion status (default false)

        Behavior:
          - If parent_id is provided, the parent must exist and belong to the user.

        Returns:
          - 201 Created with the created task.
        """
        user_id = _require_user()
        db = get_db()
        tasks = db["tasks"]

        parent_oid = _oid(parent_id)
        if parent_oid:
            parent = tasks.find_one({"_id": parent_oid, "user_id": ObjectId(user_id)})
            if not parent:
                raise ValidationError("Parent task not found or not owned by user.")

        now_iso = _now_iso()
        doc: Dict[str, Any] = {
            "user_id": ObjectId(user_id),
            "title": title,
            "description": description,
            "priority": priority,
            "estimate_minutes": estimate_minutes,
            "due_date": due_date,
            "parent_id": parent_oid,
            "completed": bool(completed),
            "created_at": now_iso,
            "updated_at": now_iso,
            "completed_at": now_iso if completed else None,
        }
        ins = tasks.insert_one(doc)
        doc["_id"] = ins.inserted_id
        return _serialize_task(doc)


@blp.route("/<string:task_id>")
class TaskResource(MethodView):
    """Retrieve, update, or delete a specific task by id."""

    # PUBLIC_INTERFACE
    @blp.response(200, TaskSchema)
    def get(self, task_id: str):
        """Get a single task.

        Query:
          - include_subtasks: If 'true', returns the task with nested subtasks recursively.

        Security:
          Requires Authorization: Bearer <token>
        """
        user_id = _require_user()
        include_subtasks = request.args.get("include_subtasks", "false").lower() == "true"
        oid = _oid(task_id)
        if oid is None:
            raise ValidationError("Invalid task id.")

        if include_subtasks:
            return _build_subtree(user_id, oid)

        db = get_db()
        tasks = db["tasks"]
        doc = tasks.find_one({"_id": oid, "user_id": ObjectId(user_id)})
        if not doc:
            raise ValidationError("Task not found.")
        return _serialize_task(doc)

    # PUBLIC_INTERFACE
    @blp.arguments(TaskUpdateSchema, as_kwargs=True)
    @blp.response(200, TaskSchema)
    def patch(self, task_id: str, **updates: Any):
        """Partially update a task.

        Notes:
          - You cannot update _id or user_id.
          - When changing parent_id, circular hierarchies are prevented.

        Security:
          Requires Authorization: Bearer <token>
        """
        user_id = _require_user()
        oid = _oid(task_id)
        if oid is None:
            raise ValidationError("Invalid task id.")
        db = get_db()
        tasks = db["tasks"]

        doc = tasks.find_one({"_id": oid, "user_id": ObjectId(user_id)})
        if not doc:
            raise ValidationError("Task not found.")

        set_ops: Dict[str, Any] = {}
        if "title" in updates:
            set_ops["title"] = updates["title"]
        if "description" in updates:
            set_ops["description"] = updates["description"]
        if "priority" in updates:
            set_ops["priority"] = updates["priority"]
        if "estimate_minutes" in updates:
            set_ops["estimate_minutes"] = updates["estimate_minutes"]
        if "due_date" in updates:
            set_ops["due_date"] = updates["due_date"]
        if "completed" in updates:
            comp_val = bool(updates["completed"])
            set_ops["completed"] = comp_val
            set_ops["completed_at"] = _now_iso() if comp_val else None

        if "parent_id" in updates:
            new_parent_oid = _oid(updates.get("parent_id"))
            if new_parent_oid:
                parent = tasks.find_one({"_id": new_parent_oid, "user_id": ObjectId(user_id)})
                if not parent:
                    raise ValidationError("Parent task not found or not owned by user.")
            _prevent_circular_parent(user_id, oid, new_parent_oid)
            set_ops["parent_id"] = new_parent_oid

        set_ops["updated_at"] = _now_iso()

        if set_ops:
            tasks.update_one({"_id": oid, "user_id": ObjectId(user_id)}, {"$set": set_ops})

        updated = tasks.find_one({"_id": oid})
        return _serialize_task(updated)

    # PUBLIC_INTERFACE
    @blp.response(204)
    def delete(self, task_id: str):
        """Delete the task.

        Query:
          - cascade: If 'true', delete the task and all descendants.
          - audit: Optional flag (no-op placeholder for future audit logging).

        Behavior:
          - If cascade=false and the task has subtasks, deletion is blocked.

        Security:
          Requires Authorization: Bearer <token>
        """
        user_id = _require_user()
        cascade = request.args.get("cascade", "false").lower() == "true"

        oid = _oid(task_id)
        if oid is None:
            raise ValidationError("Invalid task id.")
        db = get_db()
        tasks = db["tasks"]

        exists = tasks.find_one({"_id": oid, "user_id": ObjectId(user_id)}, {"_id": 1})
        if not exists:
            raise ValidationError("Task not found.")

        if cascade:
            ids = _collect_descendants(user_id, oid)
            tasks.delete_many({"_id": {"$in": ids}, "user_id": ObjectId(user_id)})
            return "", 204
        else:
            if _has_children(user_id, oid):
                raise ValidationError("Task has subtasks. Use cascade=true to delete with descendants.")
            tasks.delete_one({"_id": oid, "user_id": ObjectId(user_id)})
            return "", 204


@blp.route("/<string:task_id>/complete")
class TaskComplete(MethodView):
    """Mark a task and its descendants as completed."""

    # PUBLIC_INTERFACE
    @blp.response(200, TaskSchema)
    def post(self, task_id: str):
        """Mark the task and all descendants as completed.

        Behavior:
          - Updates 'completed' to true and sets 'completed_at' and 'updated_at'.

        Security:
          Requires Authorization: Bearer <token>
        """
        user_id = _require_user()
        oid = _oid(task_id)
        if oid is None:
            raise ValidationError("Invalid task id.")

        db = get_db()
        tasks = db["tasks"]

        base = tasks.find_one({"_id": oid, "user_id": ObjectId(user_id)})
        if not base:
            raise ValidationError("Task not found.")

        when = _now_iso()
        ids = _collect_descendants(user_id, oid)
        tasks.update_many(
            {"_id": {"$in": ids}, "user_id": ObjectId(user_id)},
            {"$set": {"completed": True, "completed_at": when, "updated_at": when}},
        )

        updated = tasks.find_one({"_id": oid})
        return _serialize_task(updated)
