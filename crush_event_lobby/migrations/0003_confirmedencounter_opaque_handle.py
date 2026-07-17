import uuid

from django.db import migrations, models


def populate_opaque_handles(apps, schema_editor):
    ConfirmedEncounter = apps.get_model("crush_event_lobby", "ConfirmedEncounter")
    for encounter in ConfirmedEncounter.objects.filter(opaque_handle__isnull=True):
        encounter.opaque_handle = uuid.uuid4()
        encounter.save(update_fields=["opaque_handle"])


class Migration(migrations.Migration):

    dependencies = [
        ("crush_event_lobby", "0002_eventrecapnotice_confirmedencounter_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="confirmedencounter",
            name="opaque_handle",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(populate_opaque_handles, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="confirmedencounter",
            name="opaque_handle",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
