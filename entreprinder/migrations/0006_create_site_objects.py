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

    # Handle crush.lu site (should be id=1 for SITE_ID setting)
    # Check if crush.lu already exists at any ID
    existing_crush = Site.objects.filter(domain='crush.lu').first()

    if existing_crush:
        # crush.lu already exists - ensure it's at id=1 or leave it alone
        if existing_crush.id != 1:
            # Delete the default site at id=1 if it exists and isn't crush.lu
            Site.objects.filter(id=1).exclude(domain='crush.lu').delete()
            # Update existing crush.lu to have correct name
            existing_crush.name = 'Crush.lu'
            existing_crush.save()
    else:
        # crush.lu doesn't exist - update or create at id=1
        if Site.objects.filter(id=1).exists():
            Site.objects.filter(id=1).update(domain='crush.lu', name='Crush.lu')
        else:
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
