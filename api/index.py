import os
from pathlib import Path

from app import app

# Vercel Python runtime expects a module-level `app` object.
# This shim keeps the FastAPI app in one place while allowing serverless deployment.
