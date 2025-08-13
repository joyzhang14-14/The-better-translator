"""
Vercel Serverless Function for Discord Webhook
Note: This is for webhook-based interactions only, not for a full Discord bot
"""

import json
import os
from http.server import BaseHTTPRequestHandler
import openai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
OPENAI_KEY = os.environ.get('OPENAI_KEY')

# Set OpenAI API key
openai.api_key = OPENAI_KEY

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        """Handle Discord webhook POST requests"""
        try:
            # Read the request body
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            # Discord ping verification
            if data.get('type') == 1:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {'type': 1}  # ACK ping
                self.wfile.write(json.dumps(response).encode())
                return
            
            # Handle interaction commands
            if data.get('type') == 2:  # Application command
                command_name = data.get('data', {}).get('name')
                
                # Process translation command
                if command_name == 'translate':
                    text = data.get('data', {}).get('options', [{}])[0].get('value', '')
                    
                    # Here you would add your translation logic
                    # For now, just echo back
                    response_text = f"Translation request received for: {text}"
                    
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    
                    response = {
                        'type': 4,  # Channel message with source
                        'data': {
                            'content': response_text
                        }
                    }
                    self.wfile.write(json.dumps(response).encode())
                    return
            
            # Default response
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {'type': 1}
            self.wfile.write(json.dumps(response).encode())
            
        except Exception as e:
            print(f"Error: {e}")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"Internal Server Error")
    
    def do_GET(self):
        """Health check endpoint"""
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Discord Bot Webhook is running")