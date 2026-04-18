"""Add PatchRelease and PatchNote models for the changelog feature.

Fields with _en/_de/_fr variants are added to support django-modeltranslation
(matching the pattern used in migration 0122 for the Newsletter model).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0123_migrate_newsletter_content_to_en'),
    ]

    operations = [
        migrations.CreateModel(
            name='PatchRelease',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.CharField(help_text="Semantic version string, e.g. 'v1.1'.", max_length=20)),
                ('slug', models.SlugField(help_text="URL slug, e.g. 'v1-1-quiz-night'.", max_length=80, unique=True)),
                ('title', models.CharField(help_text="Headline for the release, e.g. 'Quiz Night goes live'.", max_length=140)),
                ('title_en', models.CharField(help_text="Headline for the release, e.g. 'Quiz Night goes live'.", max_length=140, null=True)),
                ('title_de', models.CharField(help_text="Headline for the release, e.g. 'Quiz Night goes live'.", max_length=140, null=True)),
                ('title_fr', models.CharField(help_text="Headline for the release, e.g. 'Quiz Night goes live'.", max_length=140, null=True)),
                ('hero_summary', models.CharField(blank=True, help_text='One-line lede shown at the top of the release card.', max_length=280)),
                ('hero_summary_en', models.CharField(blank=True, help_text='One-line lede shown at the top of the release card.', max_length=280, null=True)),
                ('hero_summary_de', models.CharField(blank=True, help_text='One-line lede shown at the top of the release card.', max_length=280, null=True)),
                ('hero_summary_fr', models.CharField(blank=True, help_text='One-line lede shown at the top of the release card.', max_length=280, null=True)),
                ('released_on', models.DateField(help_text='Public release date.')),
                ('is_published', models.BooleanField(db_index=True, default=False, help_text='Toggle to show on the public /changelog/ page.')),
                ('commit_range_start', models.CharField(blank=True, max_length=40)),
                ('commit_range_end', models.CharField(blank=True, max_length=40)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Patch Release',
                'verbose_name_plural': 'Patch Releases',
                'ordering': ['-released_on', '-version'],
            },
        ),
        migrations.CreateModel(
            name='PatchNote',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('category', models.CharField(choices=[('feature', 'New Features'), ('improvement', 'Improvements'), ('fix', 'Fixes'), ('under_hood', 'Under the Hood')], db_index=True, max_length=20)),
                ('title', models.CharField(help_text='Short, warm, user-facing headline.', max_length=160)),
                ('title_en', models.CharField(help_text='Short, warm, user-facing headline.', max_length=160, null=True)),
                ('title_de', models.CharField(help_text='Short, warm, user-facing headline.', max_length=160, null=True)),
                ('title_fr', models.CharField(help_text='Short, warm, user-facing headline.', max_length=160, null=True)),
                ('body', models.TextField(blank=True, help_text='Longer plain-text description. Newlines are preserved.')),
                ('body_en', models.TextField(blank=True, help_text='Longer plain-text description. Newlines are preserved.', null=True)),
                ('body_de', models.TextField(blank=True, help_text='Longer plain-text description. Newlines are preserved.', null=True)),
                ('body_fr', models.TextField(blank=True, help_text='Longer plain-text description. Newlines are preserved.', null=True)),
                ('related_commits', models.JSONField(blank=True, default=list, help_text='List of commit SHAs that back this note.')),
                ('order', models.PositiveIntegerField(default=0)),
                ('release', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='notes', to='crush_lu.patchrelease')),
            ],
            options={
                'verbose_name': 'Patch Note',
                'verbose_name_plural': 'Patch Notes',
                'ordering': ['release', 'category', 'order', 'id'],
            },
        ),
    ]
