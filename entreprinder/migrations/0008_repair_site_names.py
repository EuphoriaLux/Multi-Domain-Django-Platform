"""Repair Site.name rows that ended up empty.

Allauth's email templates use {{ current_site.name }} for subject lines and
bodies ("Welcome to {{ site_name }} — please confirm your email"). When the
Site row for a domain has an empty name, the rendered emails show
"Welcome to  —…" with a missing site name. Sync each row's name from
azureproject.domains.DOMAINS whenever it is blank.
"""

from django.db import migrations


def repair_site_names(apps, schema_editor):
    Site = apps.get_model('sites', 'Site')

    from azureproject.domains import DOMAINS

    expected_by_domain = {domain: cfg.get('name') for domain, cfg in DOMAINS.items()}
    # Aliases (e.g. www.crush.lu -> Crush.lu) share the primary domain's name.
    for domain, cfg in DOMAINS.items():
        for alias in cfg.get('aliases', []):
            expected_by_domain.setdefault(alias, cfg.get('name'))

    for site in Site.objects.all():
        if (site.name or '').strip():
            continue
        expected = expected_by_domain.get(site.domain)
        if not expected:
            continue
        site.name = expected
        site.save(update_fields=['name'])


def noop_reverse(apps, schema_editor):
    # Repairing a name is not destructive; nothing to undo.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('entreprinder', '0007_delete_matching_models'),
        ('sites', '0002_alter_domain_unique'),
    ]

    operations = [
        migrations.RunPython(repair_site_names, noop_reverse),
    ]
