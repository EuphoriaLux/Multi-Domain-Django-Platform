"""Carry the auth permissions across the ``ConnectInterest`` → ``Interest`` rename.

``RenameModel`` (0194) renames the table, and ``contenttypes`` auto-renames the
``django_content_type`` row — but neither touches ``auth_permission`` codenames.
Without this, an existing deployment keeps the old ``*_connectinterest`` codenames
(with any coach/group grants still attached to them) while ``create_permissions``
mints fresh ``*_interest`` codenames at post-migrate, silently orphaning those
grants. Renaming the codenames in place preserves the grants.

On a fresh database there are no ``*_connectinterest`` permissions (the model is
created as ``Interest`` from the start), so this is a no-op there.
"""

from django.db import migrations

# default_permissions = add / change / delete / view
_CODENAMES = ["add", "change", "delete", "view"]


def _rename(apps, old_model, new_model):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    # ``RenameContentType`` — injected straight after 0194's ``RenameModel`` by
    # the contenttypes ``pre_migrate`` hook — has normally already moved the row
    # to ``new_model`` by the time this runs. It can leave the row under its old
    # name in two cases: contenttypes excluded by a database router, or a stale
    # target row making its save raise ``IntegrityError`` (it then reverts
    # itself). The grants hang off that row either way, so fall back to it.
    ct = (
        ContentType.objects.filter(app_label="crush_lu", model=new_model).first()
        or ContentType.objects.filter(app_label="crush_lu", model=old_model).first()
    )
    if ct is None:
        return
    for action in _CODENAMES:
        old = f"{action}_{old_model}"
        new = f"{action}_{new_model}"
        # If the target codename already exists (e.g. create_permissions ran
        # first), skip to avoid the (content_type, codename) unique collision —
        # the live permission is already present.
        if Permission.objects.filter(content_type=ct, codename=new).exists():
            continue
        Permission.objects.filter(content_type=ct, codename=old).update(codename=new)


def forwards(apps, schema_editor):
    _rename(apps, "connectinterest", "interest")


def backwards(apps, schema_editor):
    _rename(apps, "interest", "connectinterest")


class Migration(migrations.Migration):
    dependencies = [
        ("crush_lu", "0197_alter_interest_verbose_name"),
        ("auth", "0001_initial"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
