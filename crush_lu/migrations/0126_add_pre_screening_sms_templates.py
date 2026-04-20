from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("crush_lu", "0125_add_pre_screening_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="crushsiteconfig",
            name="pre_screening_reminder_sms_en",
            field=models.CharField(
                blank=True,
                default=(
                    "Hi {first_name}, {coach_name} from Crush.lu here. Before we talk, "
                    "please answer a few quick questions at {link}. It takes 3 minutes "
                    "and helps me help you. Thanks!"
                ),
                help_text="Placeholders: {first_name}, {coach_name}, {link}",
                max_length=320,
                verbose_name="Pre-screening reminder SMS (English)",
            ),
        ),
        migrations.AddField(
            model_name="crushsiteconfig",
            name="pre_screening_reminder_sms_de",
            field=models.CharField(
                blank=True,
                default=(
                    "Hallo {first_name}, hier ist {coach_name} von Crush.lu. Vor "
                    "unserem Gespraech beantworte bitte ein paar kurze Fragen unter "
                    "{link}. Es dauert 3 Minuten und hilft mir, dir zu helfen. Danke!"
                ),
                help_text="Placeholders: {first_name}, {coach_name}, {link}",
                max_length=320,
                verbose_name="Pre-screening reminder SMS (German)",
            ),
        ),
        migrations.AddField(
            model_name="crushsiteconfig",
            name="pre_screening_reminder_sms_fr",
            field=models.CharField(
                blank=True,
                default=(
                    "Bonjour {first_name}, c'est {coach_name} de Crush.lu. Avant notre "
                    "appel, merci de repondre a quelques questions rapides sur {link}. "
                    "3 minutes et ca m'aide a t'aider. Merci !"
                ),
                help_text="Placeholders: {first_name}, {coach_name}, {link}",
                max_length=320,
                verbose_name="Pre-screening reminder SMS (French)",
            ),
        ),
    ]
