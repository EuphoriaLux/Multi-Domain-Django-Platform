# Generated migration to add unique constraint on User.email
# This prevents race conditions where multiple accounts could be created with the same email

from django.db import migrations, connection


def add_unique_email_index(apps, schema_editor):
    """Add unique index on email field (case-insensitive, excluding empty emails)"""
    vendor = connection.vendor

    if vendor == 'postgresql':
        # PostgreSQL: Use partial unique index with LOWER() for case-insensitivity
        schema_editor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS auth_user_email_unique
            ON auth_user (LOWER(email))
            WHERE email IS NOT NULL AND email != '';
        """)
    elif vendor == 'sqlite':
        # SQLite 3.8.0+ supports partial indexes with WHERE clause
        # Exclude empty/null emails to allow test users without emails
        schema_editor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS auth_user_email_unique
            ON auth_user (email COLLATE NOCASE)
            WHERE email IS NOT NULL AND email != '';
        """)
    else:
        # MySQL/other: Simple unique index
        schema_editor.execute("""
            CREATE UNIQUE INDEX auth_user_email_unique
            ON auth_user (email);
        """)


def remove_unique_email_index(apps, schema_editor):
    """Remove the unique email index"""
    schema_editor.execute("DROP INDEX IF EXISTS auth_user_email_unique;")


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0028_add_is_popup_to_oauthstate'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(add_unique_email_index, remove_unique_email_index),
    ]
