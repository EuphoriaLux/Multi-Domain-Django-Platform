# Generated migration for EmailPreference model

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


def create_email_preferences_for_existing_users(apps, schema_editor):
    """
    Create EmailPreference records for all existing users.
    All notifications ON by default except marketing (GDPR compliance).
    """
    User = apps.get_model('auth', 'User')
    EmailPreference = apps.get_model('crush_lu', 'EmailPreference')

    for user in User.objects.all():
        EmailPreference.objects.get_or_create(
            user=user,
            defaults={
                'unsubscribe_token': uuid.uuid4(),
                'email_profile_updates': True,
                'email_event_reminders': True,
                'email_new_connections': True,
                'email_new_messages': True,
                'email_marketing': False,  # OFF by default - GDPR
                'unsubscribed_all': False,
            }
        )


def reverse_migration(apps, schema_editor):
    """Remove all EmailPreference records (for rollback)"""
    EmailPreference = apps.get_model('crush_lu', 'EmailPreference')
    EmailPreference.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('crush_lu', '0026_fix_storage_callable_pattern'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailPreference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('unsubscribe_token', models.UUIDField(default=uuid.uuid4, editable=False, help_text='Secure token for one-click unsubscribe links', unique=True)),
                ('email_profile_updates', models.BooleanField(default=True, help_text='Emails about profile approval, revision requests')),
                ('email_event_reminders', models.BooleanField(default=True, help_text='Reminders about upcoming events you\'re registered for')),
                ('email_new_connections', models.BooleanField(default=True, help_text='Notifications about new connection requests')),
                ('email_new_messages', models.BooleanField(default=True, help_text='Notifications about new messages from connections')),
                ('email_marketing', models.BooleanField(default=False, help_text='Marketing emails, newsletters, promotions (requires explicit opt-in)')),
                ('unsubscribed_all', models.BooleanField(default=False, help_text='User has unsubscribed from ALL emails')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(help_text='User who owns these email preferences', on_delete=django.db.models.deletion.CASCADE, related_name='email_preference', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Email Preference',
                'verbose_name_plural': '📧 Email Preferences',
                'ordering': ['-updated_at'],
            },
        ),
        # Create EmailPreference for all existing users
        migrations.RunPython(
            create_email_preferences_for_existing_users,
            reverse_migration
        ),
    ]