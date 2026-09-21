"""Remove private assets after their database deletion commits."""

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import LeadPhoto


@receiver(post_delete, sender=LeadPhoto)
def delete_photo_file(sender, instance, **kwargs):
    if instance.image.name:
        name, storage = instance.image.name, instance.image.storage
        transaction.on_commit(lambda: storage.delete(name), robust=True)
