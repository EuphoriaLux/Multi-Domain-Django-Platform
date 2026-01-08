# Generated data migration for creating Site objects
# This migration creates Site objects for all domains served by this application
# Required for django.contrib.sites and django-allauth to function properly

from django.db import migrations, connection


def create_sites(apps, schema_editor):
    """Create Site objects for all configured domains."""
    Site = apps.get_model('sites', 'Site')

    # All domains that need Site objects (excluding crush.lu which is handled separately)
    # Format: (id, domain, name) - explicitly set IDs to avoid PostgreSQL sequence issues
    other_domains = [
        (2, 'entreprinder.lu', 'Entreprinder'),
        (3, 'vinsdelux.com', 'VinsDelux'),
        (4, 'power-up.lu', 'Power-Up'),
        (5, 'powerup.lu', 'Power-Up'),
        (6, 'tableau.lu', 'Tableau'),
        (7, 'delegations.lu', 'Delegations'),
        # Development domains
        (8, 'localhost', 'Local Development'),
        (9, '127.0.0.1', 'Local Development'),
        (10, 'testserver', 'Test Server'),
        # Azure hostname - using the production default site
        (11, 'entreprinder-lunet.azurewebsites.net', 'Entreprinder (Azure)'),
    ]

    # Update the default site (id=1) to be crush.lu
    # Using filter().update() is safer than get() as it doesn't raise DoesNotExist
    Site.objects.filter(id=1).update(domain='crush.lu', name='Crush.lu')

    # If no site with id=1 existed (shouldn't happen but be safe), create it
    if not Site.objects.filter(id=1).exists():
        Site.objects.create(id=1, domain='crush.lu', name='Crush.lu')

    # Create all other sites with explicit IDs to avoid PostgreSQL sequence conflicts
    for site_id, domain, name in other_domains:
        # Skip if domain already exists
        if Site.objects.filter(domain=domain).exists():
            continue
        # If ID is already taken, create without specifying ID
        if Site.objects.filter(id=site_id).exists():
            Site.objects.create(domain=domain, name=name)
        else:
            Site.objects.create(id=site_id, domain=domain, name=name)

    # Reset PostgreSQL sequence to avoid future conflicts
    # This ensures auto-increment starts after our highest ID
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT setval(pg_get_serial_sequence('django_site', 'id'), "
                "(SELECT MAX(id) FROM django_site));"
            )


def reverse_sites(apps, schema_editor):
    """Reverse migration - reset to Django default site."""
    Site = apps.get_model('sites', 'Site')

    # Keep only the default example.com site
    Site.objects.exclude(id=1).delete()
    Site.objects.filter(id=1).update(domain='example.com', name='example.com')


class Migration(migrations.Migration):

    dependencies = [
        ('entreprinder', '0005_move_finops_models'),
        ('sites', '0002_alter_domain_unique'),
    ]

    operations = [
        migrations.RunPython(create_sites, reverse_sites),
    ]
