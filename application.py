from flask import Flask



#ignore this file its for testing

app = Flask(__name__)

@app.route('/')
def home():
    return 'Hello from Azure!'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)