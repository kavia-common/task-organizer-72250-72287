# Todo Backend API (Flask)

This is the Flask backend for the Todo application. It exposes REST APIs for:
- Health check
- Authentication: register, login, current user
- Task management with nested subtasks

## Requirements
- Python 3.11+
- A running MongoDB instance (the project includes a `todo_database` service)
- Environment variables configured (see .env.example)

## Environment Variables
Copy `.env.example` to `.env` and set appropriate values.

Required:
- JWT_SECRET: Secret key for signing JWTs
- MONGODB_URL: Mongo connection string (e.g., mongodb://todo_database:27017)
- MONGODB_DB: Database name (e.g., todo_app)

Optional:
- JWT_EXPIRES_HOURS: Token expiration in hours (default: 24)
- FLASK_RUN_HOST: Host bind (default: 0.0.0.0)
- FLASK_RUN_PORT: Port (default: 3001)
- FLASK_DEBUG: "1" for debug, else "0"

## Install dependencies
pip install -r requirements.txt

## Run
python run.py

By default the API binds to http://0.0.0.0:3001.

## Endpoints
- Health: GET /
- Auth:
  - POST /auth/register
  - POST /auth/login
  - GET  /auth/me
- Tasks:
  - GET/POST /tasks
  - GET/PATCH/DELETE /tasks/<task_id>
  - POST /tasks/<task_id>/complete

API docs (OpenAPI/Swagger UI) under /docs when running.
