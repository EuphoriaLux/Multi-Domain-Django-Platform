from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0134_merge_20260422_2116'),
    ]

    operations = [
        migrations.AlterField(
            model_name='callattempt',
            name='result',
            field=models.CharField(
                choices=[
                    ('success', 'Call Completed'),
                    ('failed', 'Call Failed'),
                    ('sms_sent', 'SMS Sent'),
                    ('whatsapp_sent', 'WhatsApp Sent'),
                    ('event_invite_sms', 'Event Invite SMS'),
                ],
                help_text='Whether the call succeeded or failed',
                max_length=20,
            ),
        ),
    ]
