from flask import Flask
from dotenv import load_dotenv

def create_app():
    load_dotenv()

    app = Flask(__name__)

    # Import and register your routes
    from .routes import api_routes
    app.register_blueprint(api_routes)

    return app
