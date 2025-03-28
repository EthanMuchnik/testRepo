from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import pickle
import os
import base64
from email.mime.text import MIMEText
import json
import time
from config import SCOPES, CREDENTIALS_FILE, TOKEN_FILE, AZURE_URL
from ai_scorer import AIScorer
import logging
from datetime import datetime, timedelta
from flask import session
from user_manager import UserManager
import google_auth_oauthlib.flow
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create a single instance of UserManager
user_manager = UserManager()

class GmailHandler:
    # Class-level dictionary to store history IDs for each user
    user_history_ids = {}
    
    def __init__(self):
        self.creds = None
        self.service = None
        self.ai_scorer = None
        self.last_history_id = None
        
        # Use environment variables instead of hardcoded keys
        self.ai_scorer = AIScorer(
            os.getenv('AZURE_OPENAI_KEY'),
            os.getenv('AZURE_OPENAI_ENDPOINT')
        )
        print(f"Created AIScorer with deployment: {os.getenv('AZURE_OPENAI_DEPLOYMENT')}")

    def setup_credentials(self):
        """Set up Gmail API credentials"""
        try:
            # Clear any existing credentials
            self.creds = None
            self.service = None
            
            # Create new flow with forced prompt for desktop client
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, 
                SCOPES,
                redirect_uri='http://localhost:8090'  # Must match OAuth client configuration
            )
            
            # Request offline access to get refresh token
            flow.oauth2session.redirect_uri = 'http://localhost:8090'
            auth_url, _ = flow.authorization_url(
                access_type='offline',
                prompt='consent'  # Force prompt to ensure we get a refresh token
            )
            
            # Use local server flow for desktop client
            self.creds = flow.run_local_server(
                port=8090,
                authorization_prompt_message='Please select a Google account',
                success_message='Authentication successful! You can close this window.',
                open_browser=True
            )
            
            # Store the token data
            token_data = {
                'token': self.creds.token,
                'refresh_token': self.creds.refresh_token,
                'token_uri': self.creds.token_uri,
                'client_id': self.creds.client_id,
                'client_secret': self.creds.client_secret,
                'scopes': self.creds.scopes
            }
            
            self.service = build('gmail', 'v1', credentials=self.creds)
            
            # Check if user exists in database
            email = self.get_user_email()
            logger.info(f"Checking if user {email} exists in database")
            
            # Store token in user manager
            if email:
                user_manager.update_user(email, {'gmail_token': token_data})
                logger.info(f"Stored token for user {email}")
            
            # Important: Return "new_user" status without re-authenticating
            if not user_manager.user_exists(email):
                logger.info(f"User {email} is new, returning new_user status")
                return "new_user"
            
            logger.info(f"User {email} exists, authentication successful")
            return None
            
        except Exception as e:
            logger.error(f"Error setting up credentials: {str(e)}")
            return str(e)

    def setup_push_notifications(self):
        """Set up Gmail API push notifications"""
        try:
            # Replace YOUR_PUBLIC_URL with the ngrok URL you got
            webhook_url = 'https://your-ngrok-url.ngrok.io/webhook'  
            
            request = {
                'labelIds': ['INBOX'],
                'topicName': 'projects/emailflow-453902/topics/gmail-notifications',
                'labelFilterAction': 'include'
            }
            
            self.service.users().watch(userId='me', body=request).execute()
            print("Push notifications set up successfully!")
        except Exception as e:
            print(f"Error setting up push notifications: {str(e)}")

    def get_email_data(self, message_id):
        max_retries = 3
        retry_delay = 1  # seconds
        
        for attempt in range(max_retries):
            try:
                message = self.service.users().messages().get(
                    userId='me', id=message_id, format='full').execute()
                
                headers = message['payload']['headers']
                subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
                sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender')
                date = next((h['value'] for h in headers if h['name'] == 'Date'), '')
                
                email_data = {
                    'message_id': message_id,
                    'subject': subject,
                    'sender': sender,
                    'date': date,
                    'snippet': message.get('snippet', ''),
                    'importance': self.ai_scorer.score_email({
                        'sender': sender,
                        'subject': subject,
                        'snippet': message.get('snippet', '')
                    })
                }
                return email_data
                
            except Exception as e:
                logger.error(f"Attempt {attempt + 1}/{max_retries} failed for email {message_id}: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    # Try to refresh credentials
                    if 'invalid credentials' in str(e).lower():
                        self.setup_credentials()
                else:
                    return None

    def get_unread_emails(self, after=None):
        """Get unread emails, optionally filtered by date"""
        try:
            if not self.service:
                self.setup_service()
            
            # Build the query
            query = 'is:unread in:inbox'
            
            # Add date filter if provided
            if after:
                # Format date as YYYY/MM/DD
                date_str = after.strftime('%Y/%m/%d')
                query += f' after:{date_str}'
            
            # Get message IDs
            results = self.service.users().messages().list(
                userId='me',
                q=query,
                maxResults=100  # Limit to 100 messages
            ).execute()
            
            # Extract message IDs
            messages = results.get('messages', [])
            return [msg['id'] for msg in messages]
            
        except Exception as e:
            logger.error(f"Error getting unread emails: {str(e)}")
            return []

    def get_history(self):
        """Get changes since last check"""
        try:
            if not self.last_history_id:
                # Get the current history ID if we don't have one
                profile = self.service.users().getProfile(userId='me').execute()
                self.last_history_id = profile['historyId']
                return []

            results = self.service.users().history().list(
                userId='me',
                startHistoryId=self.last_history_id
            ).execute()

            changes = results.get('history', [])
            if changes:
                self.last_history_id = results['historyId']
            
            return changes
        except Exception as e:
            print(f"Error getting history: {str(e)}")
            return []

    def check_new_emails(self):
        """Check for new emails using history"""
        changes = self.get_history()
        new_messages = []
        
        for change in changes:
            if 'messagesAdded' in change:
                for message in change['messagesAdded']:
                    msg_data = self.get_email_data(message['message']['id'])
                    if msg_data:
                        new_messages.append(msg_data)
        
        return new_messages

    def get_user_email(self):
        """Get user's email address"""
        try:
            profile = self.service.users().getProfile(userId='me').execute()
            email = profile['emailAddress']
            logger.info(f"Retrieved email: {email}")
            return email
        except Exception as e:
            logger.error(f"Error getting user email: {str(e)}")
            return None

    def get_filtered_unread_emails(self, timeframe):
        """Get unread emails within timeframe and process them one by one"""
        try:
            # Calculate time threshold based on filter
            now = datetime.now()
            if timeframe == 'hour':
                threshold = now - timedelta(hours=1)
            elif timeframe == 'day':
                threshold = now - timedelta(days=1)
            elif timeframe == 'week':
                threshold = now - timedelta(weeks=1)
            elif timeframe == 'month':
                threshold = now - timedelta(days=30)
            
            # Get unread emails
            results = self.service.users().messages().list(
                userId='me',
                labelIds=['UNREAD'],
                q=f'after:{threshold.strftime("%Y/%m/%d")}'
            ).execute()
            
            messages = results.get('messages', [])
            
            # Process each message one at a time
            for message in messages:
                # Get email details
                email_data = self.get_email_data(message['id'])
                if email_data:
                    # Yield each processed email immediately
                    yield email_data
                
        except Exception as e:
            logger.error(f"Error getting filtered unread emails: {str(e)}")
            yield None 

    def get_unread_count(self, timeframe):
        """Get total count of unread emails within timeframe"""
        try:
            now = datetime.now()
            if timeframe == 'hour':
                threshold = now - timedelta(hours=1)
            elif timeframe == 'day':
                threshold = now - timedelta(days=1)
            elif timeframe == 'week':
                threshold = now - timedelta(weeks=1)
            elif timeframe == 'month':
                threshold = now - timedelta(days=30)
            
            results = self.service.users().messages().list(
                userId='me',
                labelIds=['UNREAD'],
                q=f'after:{threshold.strftime("%Y/%m/%d")}'
            ).execute()
            
            return len(results.get('messages', []))
        except Exception as e:
            logger.error(f"Error getting unread count: {str(e)}")
            return 0 

    def mark_as_read(self, message_id):
        """Mark a message as read by removing the UNREAD label"""
        try:
            if not self.service:
                self.setup_service()
            
            # Create the label modification object
            mods = {
                'removeLabelIds': ['UNREAD'],
                'addLabelIds': []
            }
            
            # Execute the modification
            self.service.users().messages().modify(
                userId='me',
                id=message_id,
                body=mods
            ).execute()
            
            return True
            
        except Exception as e:
            logger.error(f"Error marking message {message_id} as read: {str(e)}")
            return False

    def check_for_new_emails(self):
        """Check for new emails and process them
        
        Returns:
            bool: True if new emails were found and processed, False otherwise
        """
        try:
            # Get the user's email address
            user_email = self.get_user_email()
            if not user_email:
                logger.error("Could not retrieve user email")
                return False
            
            # Get the current history ID
            current_history_id = self.get_latest_history_id()
            if not current_history_id:
                logger.error("Could not retrieve history ID")
                return False
            
            # Get the last history ID for this user
            last_history_id = GmailHandler.user_history_ids.get(user_email)
            
            # If this is the first check, store the history ID and return
            if not last_history_id:
                logger.info(f"First check - storing history ID for future reference")
                GmailHandler.user_history_ids[user_email] = current_history_id
                return False
            
            # If the history ID hasn't changed, no new emails
            if current_history_id == last_history_id:
                # Reduce verbosity - don't log this message
                # logger.info(f"No changes in history ID - no new emails")
                return False
            
            # History ID has changed, check for new messages
            logger.info(f"History ID changed from {last_history_id} to {current_history_id}")
            
            # Get history since last check
            history_results = self.service.users().history().list(
                userId='me', 
                startHistoryId=last_history_id
            ).execute()
            
            # Update the stored history ID
            GmailHandler.user_history_ids[user_email] = current_history_id
            
            # Check if there are any history records
            if 'history' not in history_results:
                logger.info("No history records found")
                return False
            
            # Process new messages
            new_message_ids = set()
            for history in history_results.get('history', []):
                for message_added in history.get('messagesAdded', []):
                    msg = message_added.get('message', {})
                    if 'INBOX' in msg.get('labelIds', []):
                        new_message_ids.add(msg.get('id'))
            
            # Process each new message
            if new_message_ids:
                logger.info(f"Found {len(new_message_ids)} new messages")
                for message_id in new_message_ids:
                    self.process_message(message_id)
                return True
            else:
                logger.info("No new inbox messages found in history")
                return False
            
        except Exception as e:
            logger.error(f"Error checking for new emails: {str(e)}")
            return False

    def get_latest_history_id(self):
        """Get the latest history ID for the user's mailbox"""
        try:
            # Create a new service instance for this request
            service = build('gmail', 'v1', credentials=self.creds)
            
            # Get the profile which includes the historyId
            profile = service.users().getProfile(userId='me').execute()
            
            # Close the service connection
            if hasattr(service, '_http'):
                service._http.close()
            
            return profile.get('historyId')
            
        except Exception as e:
            logger.error(f"Error getting latest history ID: {str(e)}")
            return None
        finally:
            # Ensure we clean up any remaining connections
            if 'service' in locals() and hasattr(service, '_http'):
                service._http.close()

    def get_history_since_last_id(self):
        """Get history since the last recorded history ID"""
        if not self.last_history_id:
            logger.error("No last history ID available")
            return None
        
        try:
            # Create a new service instance for this request
            service = build('gmail', 'v1', credentials=self.creds)
            
            # Get history with the last history ID
            history = service.users().history().list(
                userId='me',
                startHistoryId=self.last_history_id,
                historyTypes=['messageAdded']
            ).execute()
            
            # Close the service connection
            if hasattr(service, '_http'):
                service._http.close()
            
            return history
            
        except Exception as e:
            logger.error(f"Error getting history since last ID: {str(e)}")
            return None
        finally:
            # Ensure we clean up any remaining connections
            if 'service' in locals() and hasattr(service, '_http'):
                service._http.close()

    def extract_new_message_ids(self, history_results):
        """Extract new message IDs from history results"""
        if not history_results or 'history' not in history_results:
            return []
        
        message_ids = set()
        
        # Extract message IDs from history
        for item in history_results.get('history', []):
            for message_added in item.get('messagesAdded', []):
                message = message_added.get('message', {})
                
                # Only include messages that are not in TRASH and are UNREAD
                labels = message.get('labelIds', [])
                if 'TRASH' not in labels and 'UNREAD' in labels:
                    message_ids.add(message.get('id'))
        
        return list(message_ids) 

    def process_message(self, message_id):
        """Process a single message by ID
        
        Args:
            message_id (str): Gmail message ID
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Get the message details
            message = self.service.users().messages().get(userId='me', id=message_id).execute()
            
            # Extract message data
            headers = message.get('payload', {}).get('headers', [])
            
            # Get subject and sender
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'No Subject')
            sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unknown Sender')
            
            # Get snippet
            snippet = message.get('snippet', '')
            
            # Get the current user's email
            user_email = self.get_user_email()
            
            # Get user data from database
            user_data = user_manager.get_user(user_email)
            
            # Check if user has enhanced AI model enabled
            ai_settings = user_data.get('ai_settings', {})
            selected_model = ai_settings.get('selected_model', 'standard')
            
            # Get user profile if enhanced model is selected
            user_profile = None
            if selected_model == 'enhanced':
                profile = ai_settings.get('profile', {})
                if profile.get('training_status') == 'completed':
                    user_profile = profile.get('profile_text')
            
            # Score the email importance
            importance = self.ai_scorer.score_email({
                'sender': sender,
                'subject': subject,
                'snippet': snippet
            }, user_profile)
            
            print(f"Email from {sender}")
            print(f"Subject: {subject}")
            print(f"Importance score: {importance['score']}/10 - {importance['explanation']}")
            print(f"Model used: {selected_model}")
            
            # If importance score is high (7 or above), send SMS notification
            if importance['score'] >= 7:
                print(f"High importance email detected! Score: {importance['score']}/10")
                
                # Get user's phone number from database
                user_data = user_manager.get_user(user_email)
                if user_data and user_data.get('phonenumber'):
                    phone_number = user_data.get('phonenumber')
                    logger.info(f"Found phone number for user {user_email}: {phone_number}")
                    
                    # Import here to avoid circular imports
                    from sms_handler import SMSHandler
                    sms_handler = SMSHandler()
                    
                    # Send SMS notification using the SMS handler
                    logger.info(f"Attempting to send SMS notification to {phone_number}")
                    result = sms_handler.send_high_priority_email_alert(
                        phone_number, 
                        sender, 
                        subject, 
                        importance['score']
                    )
                    
                    if result:
                        print(f"SMS notification sent to {phone_number}")
                        logger.info(f"SMS notification successfully sent to {phone_number}")
                    else:
                        print(f"Failed to send SMS notification to {phone_number}")
                        logger.error(f"Failed to send SMS notification to {phone_number}")
                else:
                    print("No phone number found for user")
                    logger.warning(f"No phone number found for user {user_email}")
            
            print(f"==== EMAIL PROCESSING COMPLETE ====\n")
            return True
            
        except Exception as e:
            logger.error(f"Error processing message {message_id}: {str(e)}")
            print(f"ERROR processing message {message_id}: {str(e)}")
            return False 

    def setup_service(self):
        """Set up the Gmail service using existing credentials"""
        if self.creds and not self.service:
            self.service = build('gmail', 'v1', credentials=self.creds)
        return self.service 