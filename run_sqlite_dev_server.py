import os
import sys

def main():
    # Only for Local Development - Load environment variables from the .env file
    if 'WEBSITE_HOSTNAME' not in os.environ:
        from dotenv import load_dotenv
        load_dotenv('./.env')
    
    # Overwrite DBHOST with empty string to force SQLite fallback
    os.environ['DBHOST'] = ''

    # Ensure DJANGO_SETTINGS_MODULE is set
    settings_module = "azureproject.settings"
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', settings_module)

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django."
        ) from exc

    execute_from_command_line(sys.argv)

if __name__ == '__main__':
    main()
