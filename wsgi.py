from app import app

# This is for Vercel serverless functions
def handler(request, context):
    return app(request, context) 