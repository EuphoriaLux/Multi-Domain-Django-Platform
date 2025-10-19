# Generated manually for performance optimization
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('crush_lu', '0016_eventinvitation_special_user_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='eventregistration',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending Payment'),
                    ('confirmed', 'Confirmed'),
                    ('waitlist', 'Waitlist'),
                    ('cancelled', 'Cancelled'),
                    ('attended', 'Attended'),
                    ('no_show', 'No Show')
                ],
                db_index=True,
                default='pending',
                max_length=20
            ),
        ),
        migrations.AlterField(
            model_name='profilesubmission',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending Review'),
                    ('approved', 'Approved'),
                    ('rejected', 'Rejected'),
                    ('revision', 'Needs Revision')
                ],
                db_index=True,
                default='pending',
                max_length=20
            ),
        ),
    ]
