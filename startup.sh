#!/bin/bash
echo "Starting script..."
echo "Startup script has run at $(date)" >> /home/LogFiles/startup.log
cd /home/site/wwwroot
pip install --upgrade pip
pip install -r requirements.txt
gunicorn --bind=0.0.0.0 --timeout 600 --worker-class eventlet --log-level debug application:app 
