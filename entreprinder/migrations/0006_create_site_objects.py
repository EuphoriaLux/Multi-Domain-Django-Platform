# Generated data migration for creating Site objects
# This migration creates Site objects for all domains served by this application
# Required for django.contrib.sites and django-allauth to function properly

from django.db import migrations


def create_sites(apps, schema_editor):
    """Create Site objects for all configured domains."""
    Site = apps.get_model('sites', 'Site')

    # All domains that need Site objects
    # Format: (domain, name)
    domains = [
        ('crush.lu', 'Crush.lu'),
        ('entreprinder.lu', 'Entreprinder'),
        ('vinsdelux.com', 'VinsDelux'),
        ('power-up.lu', 'Power-Up'),
        ('powerup.lu', 'Power-Up'),
        ('tableau.lu', 'Tableau'),
        # Development domains
        ('localhost', 'Local Development'),
        ('127.0.0.1', 'Local Development'),
        ('testserver', 'Test Server'),
        # Azure hostname - using the production default site
        ('entreprinder-lunet.azurewebsites.net', 'Entreprinder (Azure)'),
    ]

    # Check if crush.lu already exists (from tests or previous setup)
    existing_crush = Site.objects.filter(domain='crush.lu').first()

    if existing_crush:
        # crush.lu already exists - ensure it's the default (id=1) if possible
        # Just update its name to be consistent
        existing_crush.name = 'Crush.lu'
        existing_crush.save()
    else:
        # Update or create the default site (id=1) to be crush.lu
        default_site = Site.objects.filter(id=1).first()
        if default_site:
            # Check if changing this domain would cause a conflict
            if not Site.objects.filter(domain='crush.lu').exists():
                default_site.domain = 'crush.lu'
                default_site.name = 'Crush.lu'
                default_site.save()
        else:
            Site.objects.create(id=1, domain='crush.lu', name='Crush.lu')

    # Create all other sites (skip crush.lu since it's handled above)
    for domain, name in domains:
        if domain != 'crush.lu':
            Site.objects.update_or_create(
                domain=domain,
                defaults={'name': name}
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
