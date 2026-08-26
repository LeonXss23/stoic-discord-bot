from http.server import BaseHTTPRequestHandler
import asyncio
import os
import sys

# Add parent directory to path so bot.py modules can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import Config, StoicBotService

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            config = Config()
            config.validate_for_posting()
            service = StoicBotService(config)
            
            # Run one-shot routine
            success = asyncio.run(service.execute_daily_routine())
            
            if success:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "success", "message": "Quote posted to Discord"}')
            else:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status": "error", "message": "Failed to scrape or post quote"}')
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(f'{{"status": "error", "message": "{str(e)}"}}'.encode())
