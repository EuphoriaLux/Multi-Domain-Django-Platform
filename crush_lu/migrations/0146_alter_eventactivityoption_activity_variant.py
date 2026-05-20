# Generated migration

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0145_globalactivityoption_display_name_fr'),
    ]

    operations = [
        migrations.AlterField(
            model_name='eventactivityoption',
            name='activity_variant',
            field=models.CharField(
                blank=True,
                choices=[
                    ('music', 'With Favorite Music'),
                    ('questions', '5 Predefined Questions'),
                    ('picture_story', 'Share Favorite Picture & Story'),
                    ('spicy_questions', 'Spicy Questions First'),
                    ('forbidden_word', 'Forbidden Word Challenge'),
                    ('open_conversation', 'Open Conversation'),
                    ('theme_based', 'Theme Based Conversation'),
                ],
                help_text='Sub-option for the activity',
                max_length=20,
            ),
        ),
    ]
