from api import create_app
from flask_cors import CORS

# Create Flask app from factory
app = create_app()

# Enable CORS for frontend (React running on localhost:5173)
CORS(app, resources={
    r"/api/*": {
        "origins": [
            "http://localhost:5173",
            "https://69318f474e4b9914a4013048--hnu-unerversity.netlify.app"
        ]
    }
})
if __name__ == "__main__":
    # Run the Flask app
    app.run(host="0.0.0.0", port=5000, debug=True)
