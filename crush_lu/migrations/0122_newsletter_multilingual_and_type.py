# Generated manually for newsletter multilingual support and type field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0121_add_candidate_note_to_submission'),
    ]

    operations = [
        # newsletter_type field
        migrations.AddField(
            model_name='newsletter',
            name='newsletter_type',
            field=models.CharField(
                choices=[('standard', 'Standard Newsletter'), ('patch_notes', 'Patch Notes')],
                default='standard',
                help_text='Template style for the newsletter email',
                max_length=20,
            ),
        ),
        # subject translation fields
        migrations.AddField(
            model_name='newsletter',
            name='subject_en',
            field=models.CharField(max_length=200, null=True),
        ),
        migrations.AddField(
            model_name='newsletter',
            name='subject_de',
            field=models.CharField(max_length=200, null=True),
        ),
        migrations.AddField(
            model_name='newsletter',
            name='subject_fr',
            field=models.CharField(max_length=200, null=True),
        ),
        # body_html translation fields
        migrations.AddField(
            model_name='newsletter',
            name='body_html_en',
            field=models.TextField(
                blank=True,
                help_text='Newsletter body content (HTML). Auto-generated when event is selected.',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='newsletter',
            name='body_html_de',
            field=models.TextField(
                blank=True,
                help_text='Newsletter body content (HTML). Auto-generated when event is selected.',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='newsletter',
            name='body_html_fr',
            field=models.TextField(
                blank=True,
                help_text='Newsletter body content (HTML). Auto-generated when event is selected.',
                null=True,
            ),
        ),
        # body_text translation fields
        migrations.AddField(
            model_name='newsletter',
            name='body_text_en',
            field=models.TextField(
                blank=True,
                help_text='Plain text fallback (auto-stripped from HTML if blank)',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='newsletter',
            name='body_text_de',
            field=models.TextField(
                blank=True,
                help_text='Plain text fallback (auto-stripped from HTML if blank)',
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='newsletter',
            name='body_text_fr',
            field=models.TextField(
                blank=True,
                help_text='Plain text fallback (auto-stripped from HTML if blank)',
                null=True,
            ),
        ),
    ]
