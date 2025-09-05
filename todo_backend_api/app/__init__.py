from flask import Flask
from flask_cors import CORS
from flask_smorest import Api
import os

from .routes.health import blp as health_blp
from .routes.auth import blp as auth_blp
from .services.db import init_db

# Create and configure Flask app
app = Flask(__name__)
app.url_map.strict_slashes = False

# CORS - allow all origins for now; refine later if needed
CORS(app, resources={r"/*": {"origins": "*"}})

# OpenAPI / API documentation configuration
app.config["API_TITLE"] = "My Flask API"
app.config["API_VERSION"] = "v1"
app.config["OPENAPI_VERSION"] = "3.0.3"
app.config["OPENAPI_URL_PREFIX"] = "/docs"
app.config["OPENAPI_SWAGGER_UI_PATH"] = ""
app.config["OPENAPI_SWAGGER_UI_URL"] = "https://cdn.jsdelivr.net/npm/swagger-ui-dist/"

# JWT configuration from environment (required)
# Do not hardcode secrets; JWT_SECRET must be provided in environment or .env.
jwt_secret = os.getenv("JWT_SECRET")
if not jwt_secret:
    raise RuntimeError("JWT_SECRET is not set. Please set it in the environment.")
app.config["JWT_SECRET"] = jwt_secret
# Optional expiration hours (default 24); used by routes/auth.py helper
app.config["JWT_EXPIRES_HOURS"] = int(os.getenv("JWT_EXPIRES_HOURS", "24"))

# Initialize MongoDB (reads MONGODB_URL and MONGODB_DB from environment)
# This will raise a RuntimeError if these variables are missing, making the
# failure explicit during startup.
init_db(app)

# Register API with flask-smorest and blueprints
api = Api(app)
api.register_blueprint(health_blp)
api.register_blueprint(auth_blp)
