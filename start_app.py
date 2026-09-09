#!/usr/bin/env python
"""
Start Sentinel app without Docker.
"""

import os
import sys
import subprocess
import psycopg2
from pathlib import Path

# Configuration
DB_URL = "postgresql://postgres:postgres@localhost:5432/sentinel"
REPO_ROOT = Path(__file__).resolve().parent

def check_postgres():
    """Check if PostgreSQL is running."""
    try:
        conn = psycopg2.connect(
            host="localhost",
            port=5432,
            user="postgres",
            password="postgres",
            database="postgres"
        )
        conn.close()
        return True
    except:
        return False

def main():
    print("🚀 Starting Sentinel Application")
    print("─" * 50)
    
    # Check PostgreSQL
    print("🔍 Checking PostgreSQL...")
    if not check_postgres():
        print("❌ PostgreSQL not running")
        print("Please start PostgreSQL service:")
        print("1. Open Services (services.msc)")
        print("2. Start 'postgresql-x64-18' service")
        sys.exit(1)
    print("✅ PostgreSQL is running")
    
    # Set environment
    env = os.environ.copy()
    env["DATABASE_URL"] = DB_URL
    env["GRID_HOST"] = "localhost:8000"
    env["PYTHONPATH"] = str(REPO_ROOT)
    
    # Install dependencies if needed
    print("\n📦 Checking dependencies...")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "model1-registry/requirements.txt", "-q"], 
                      check=False)
    except:
        print("⚠️  Could not install dependencies automatically")
    
    # Start FastAPI app
    print("\n🌐 Starting FastAPI server...")
    print(f"📍 Open in browser: http://localhost:8000")
    print(f"📍 Live Grid: http://localhost:8000/grid")
    print(f"📍 Live Detection: http://localhost:8000/detection")
    print("─" * 50)
    
    try:
        subprocess.run([
            sys.executable, "-m", "uvicorn",
            "model1_registry.app.main:app",
            "--host", "0.0.0.0",
            "--port", "8000",
            "--reload"
        ], cwd=str(REPO_ROOT), env=env)
    except KeyboardInterrupt:
        print("\n👋 Server stopped")
    except Exception as e:
        print(f"❌ Failed to start app: {e}")

if __name__ == "__main__":
    main()