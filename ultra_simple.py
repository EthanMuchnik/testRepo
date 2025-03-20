import sys
print("Python version:", sys.version)
import os
print("Working directory:", os.getcwd())
from flask import Flask
print("Flask imported successfully")
app = Flask(__name__)
@app.route("/")
def home():
    return "Hello World!"
application = app
