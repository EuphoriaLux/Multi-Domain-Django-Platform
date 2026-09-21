"""Private enquiry assets. Never fall back to the public media container."""

from django.core.files.storage import FileSystemStorage, storages
from storages.backends.azure_storage import AzureStorage


class LocalPrivateStorage(FileSystemStorage):
    def url(self, name):
        raise ValueError("Enquiry photos require an authorised view")


class AzurePrivateStorage(AzureStorage):
    """Fail closed if an operator accidentally configures a public container."""

    def _check_private(self):
        if self.client.get_container_properties().get("public_access"):
            raise ValueError("Arborist storage container must be private")

    def _save(self, name, content):
        self._check_private()
        return super()._save(name, content)

    def _open(self, name, mode="rb"):
        self._check_private()
        return super()._open(name, mode)

    def url(self, name, **kwargs):
        raise ValueError("Enquiry photos require an authorised view")


def private_storage():
    return storages["arborist_private"]
