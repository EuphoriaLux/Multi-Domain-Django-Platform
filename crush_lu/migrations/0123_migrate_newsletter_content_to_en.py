# Data migration: copy existing Newsletter content to _en fields.
# modeltranslation reads from language-specific fields, so existing
# data must be populated into the English variants.

from django.db import migrations


def copy_content_to_en_fields(apps, schema_editor):
    """Copy existing subject/body_html/body_text to their _en variants."""
    Newsletter = apps.get_model('crush_lu', 'Newsletter')
    for obj in Newsletter.objects.all():
        obj.subject_en = obj.subject
        obj.body_html_en = obj.body_html
        obj.body_text_en = obj.body_text
        obj.save(update_fields=['subject_en', 'body_html_en', 'body_text_en'])


def reverse_copy_en_to_original(apps, schema_editor):
    """Reverse: copy _en fields back to original fields."""
    Newsletter = apps.get_model('crush_lu', 'Newsletter')
    for obj in Newsletter.objects.all():
        obj.subject = obj.subject_en or obj.subject
        obj.body_html = obj.body_html_en or obj.body_html
        obj.body_text = obj.body_text_en or obj.body_text
        obj.save(update_fields=['subject', 'body_html', 'body_text'])


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0122_newsletter_multilingual_and_type'),
    ]

    operations = [
        migrations.RunPython(copy_content_to_en_fields, reverse_copy_en_to_original),
    ]
