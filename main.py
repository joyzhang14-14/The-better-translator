#!/usr/bin/env python3
"""
Main entry point for Railway deployment
Starts both the Discord bot and health check server
"""

import os
import sys

# Set default port if not provided by Railway
if 'PORT' not in os.environ:
    os.environ['PORT'] = '3000'
    print(f"PORT not set, using default: 3000")
else:
    print(f"Using Railway PORT: {os.environ['PORT']}")

# Import and run the bot
from bot import main

if __name__ == "__main__":
    try:
        print("Starting Discord Translator Bot with Health Server...")
        print(f"Health check will be available on port {os.environ['PORT']}")
        main()
    except Exception as e:
        print(f"Failed to start bot: {e}")
        sys.exit(1)