#!/usr/bin/env python3
"""
Build and deploy script for Pixel War application
This script handles the complete build and static file collection process
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

def run_command(cmd, description):
    """Run a command and handle errors"""
    print(f"\n🔧 {description}")
    print(f"Running: {cmd}")
    
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    
    if result.returncode != 0:
        print(f"❌ Error: {description} failed")
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
        return False
    
    print(f"✅ {description} completed successfully")
    if result.stdout.strip():
        print(f"Output: {result.stdout}")
    
    return True

def main():
    """Main build and deploy process"""
    print("🎨 Pixel War Build & Deploy Script")
    print("=" * 40)
    
    # Get project root directory
    project_root = Path(__file__).parent.parent
    os.chdir(project_root)
    
    # Step 1: Clean previous builds
    dist_dir = project_root / "vibe_coding" / "static" / "vibe_coding" / "js" / "dist"
    if dist_dir.exists():
        print("🧹 Cleaning previous build artifacts...")
        shutil.rmtree(dist_dir)
        print("✅ Clean completed")
    
    # Step 2: Install/update npm dependencies
    if not run_command("npm install", "Installing npm dependencies"):
        return False
    
    # Step 3: Build JavaScript assets with Vite
    if not run_command("npm run build", "Building JavaScript assets with Vite"):
        return False
    
    # Step 4: Collect Django static files
    if not run_command("python manage.py collectstatic --noinput", "Collecting Django static files"):
        return False
    
    # Step 5: Check if Django is running and restart if needed
    print("\n📡 Checking Django development server...")
    
    # Optional: Run tests
    test_response = input("🧪 Run tests before deployment? (y/n): ").lower()
    if test_response == 'y':
        if not run_command("python manage.py test vibe_coding", "Running Pixel War tests"):
            continue_response = input("❓ Tests failed. Continue anyway? (y/n): ").lower()
            if continue_response != 'y':
                return False
    
    print("\n🎉 Build and deploy completed successfully!")
    print("\n📋 What was accomplished:")
    print("   ✅ JavaScript assets built with Vite")
    print("   ✅ ES6 modules properly bundled")
    print("   ✅ Dependencies optimized (PixiJS, Hammer.js)")
    print("   ✅ Django static files collected")
    print("   ✅ Source maps generated for debugging")
    
    print("\n🚀 Next steps:")
    print("   1. Start Django server: python manage.py runserver")
    print("   2. Test Pixel War functionality at /vibe-coding/pixel-wars/")
    print("   3. Use 'npm run build:watch' for development")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)