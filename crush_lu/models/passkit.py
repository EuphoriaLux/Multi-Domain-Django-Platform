from django.db import models


class PasskitDeviceRegistration(models.Model):
    device_library_identifier = models.CharField(
        max_length=255,
        help_text="Apple Wallet device library identifier."
    )
    pass_type_identifier = models.CharField(
        max_length=255,
        help_text="Pass type identifier used for APNS topic."
    )
    serial_number = models.CharField(
        max_length=64,
        help_text="Pass serial number used to target updates."
    )
    push_token = models.CharField(
        max_length=255,
        help_text="APNS push token for the device."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "PassKit Device Registration"
        verbose_name_plural = "PassKit Device Registrations"
        unique_together = ("device_library_identifier", "serial_number")
        indexes = [
            models.Index(fields=["pass_type_identifier", "serial_number"]),
            models.Index(fields=["device_library_identifier", "pass_type_identifier"]),
        ]

    def __str__(self):
        return (
            f"{self.device_library_identifier} -> "
            f"{self.pass_type_identifier}:{self.serial_number}"
        )
