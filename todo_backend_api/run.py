from app import app
import os

if __name__ == "__main__":
    # Allow overriding host/port via env; default to container-friendly 0.0.0.0:3001
    host = os.getenv("FLASK_RUN_HOST", "0.0.0.0")
    port_str = os.getenv("FLASK_RUN_PORT", os.getenv("PORT", "3001"))
    try:
        port = int(port_str)
    except ValueError:
        port = 3001
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug, threaded=True)
