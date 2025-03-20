import os
from dotenv import load_dotenv
#config structure

load_dotenv()  # Load environment variables from .env file

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify'
]
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'
CLIENT_ID = os.getenv('CLIENT_ID')
AZURE_OPENAI_KEY = os.getenv('AZURE_OPENAI_KEY')
AZURE_OPENAI_ENDPOINT = os.getenv('AZURE_OPENAI_ENDPOINT')
AZURE_OPENAI_DEPLOYMENT = os.getenv('AZURE_OPENAI_DEPLOYMENT')  # Get from environment variable

# Check if running on Azure
IS_AZURE = os.getenv('WEBSITE_HOSTNAME') is not None

# For local development with desktop OAuth client or Azure App Service
if IS_AZURE:
    # Get the Azure website URL from environment variable
    AZURE_URL = f"https://{os.getenv('WEBSITE_HOSTNAME')}"
else:
    # Local development URL
    AZURE_URL = "http://localhost:8090"  # This should match your OAuth client's redirect URI
