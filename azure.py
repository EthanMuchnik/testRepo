import os
from app import app, socketio

if __name__ == '__main__':
    # For local testing
    socketio.run(app, host='0.0.0.0', port=8000, debug=True)
else:
    # For Azure App Service
    app = app
