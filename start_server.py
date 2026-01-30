#!/usr/bin/env python
"""
Wrapper script to start the development server with domain information.
This explicitly uses our custom runserver command.
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'azureproject.settings')
django.setup()

# Import and run our custom command
from core.management.commands.runserver import Command

if __name__ == '__main__':
    command = Command()
    command.run_from_argv(sys.argv)
