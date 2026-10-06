from django.conf import settings
from django.core.files.storage import storages
from django.test import SimpleTestCase, override_settings


class ArboristPrivateStorageConfigTests(SimpleTestCase):
    """The test harness must keep enquiry photos on their fail-closed backend."""

    def test_harness_keeps_local_private_backend(self):
        self.assertEqual(
            settings.STORAGES["arborist_private"]["BACKEND"],
            "arborist.storage.LocalPrivateStorage",
        )

    def test_rebuilt_storage_handler_still_refuses_urls(self):
        # Any STATIC_URL/STORAGES override resets Django's storage handler,
        # which then rebuilds every alias from settings.STORAGES.
        with override_settings(STATIC_URL="/x/"):
            pass

        storage = storages["arborist_private"]
        self.assertEqual(storage.location, str(settings.BASE_DIR / "private-arborist"))
        with self.assertRaises(ValueError):
            storage.url("a.jpg")
