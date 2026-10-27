import os
import sys
from pathlib import Path

# Add project root directory to sys.path so server and core modules can be resolved
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Import the FastAPI ASGI application
from server.main import app

# Export for Vercel Serverless Function
handler = app
