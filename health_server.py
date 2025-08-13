"""
Simple HTTP health check server for UptimeRobot monitoring
Runs alongside the Discord bot to provide a health endpoint
"""

from aiohttp import web
import asyncio
import os

# Store bot status
bot_status = {"running": False, "last_heartbeat": None}

def update_bot_status(running=True):
    """Update bot status from main bot"""
    import time
    bot_status["running"] = running
    bot_status["last_heartbeat"] = time.time()

async def health_check(request):
    """Health check endpoint for monitoring"""
    import time
    current_time = time.time()
    
    # Check if bot has sent heartbeat in last 60 seconds
    if bot_status["last_heartbeat"]:
        time_since_heartbeat = current_time - bot_status["last_heartbeat"]
        if time_since_heartbeat > 60:
            return web.Response(text="Bot not responding", status=503)
    
    if bot_status["running"]:
        return web.Response(text="Bot is running", status=200)
    else:
        return web.Response(text="Bot is starting", status=503)

async def index(request):
    """Root endpoint"""
    return web.Response(text="Discord Translator Bot Health Check Server", status=200)

async def start_health_server():
    """Start the health check server"""
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/health', health_check)
    
    # Railway provides PORT environment variable
    port = int(os.environ.get('PORT', 8080))
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    print(f"Health check server running on port {port}")
    return runner