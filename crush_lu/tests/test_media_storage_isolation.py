"""Media fields must stay on the local filesystem under pytest.

Fields such as ``MeetupEvent.image`` take a factory
(``storage=crush_media_storage``) that Django calls while importing models,
before conftest.py can rewrite STORAGES. The factories used to build their
Azure backend unconditionally, so saving such a field in a test built an Azure
client for whatever AZURE_ACCOUNT_NAME the environment held (a developer's real
account, or none) and waited out the SDK's retries.
"""

import io
from pathlib import Path
from unittest import mock

import pytest
from django.apps import apps
from django.conf import settings as django_settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db.models import FileField
from PIL import Image
from storages.backends.azure_storage import AzureStorage

from azureproject.storage_shared import SharedMediaStorage, get_shared_media_storage
from crush_lu.models import MeetupEvent
from crush_lu.storage import CrushMediaStorage, get_crush_media_storage
from entreprinder.storage import (
    EntreprinderMediaStorage,
    get_entreprinder_media_storage,
)
from power_up.storage import (
    FinOpsStorage,
    PowerUpMediaStorage,
    get_finops_storage,
    get_powerup_media_storage,
)

FACTORIES = [
    pytest.param(get_crush_media_storage, CrushMediaStorage, id="crush_media"),
    pytest.param(
        get_entreprinder_media_storage,
        EntreprinderMediaStorage,
        id="entreprinder_media",
    ),
    pytest.param(get_powerup_media_storage, PowerUpMediaStorage, id="powerup_media"),
    pytest.param(get_finops_storage, FinOpsStorage, id="powerup_finops"),
    pytest.param(get_shared_media_storage, SharedMediaStorage, id="shared_media"),
]


@pytest.fixture
def azure_account(settings):
    """An Azure account configured, as a developer's .env may do."""
    settings.AZURE_ACCOUNT_NAME = "realaccount"
    settings.AZURE_ACCOUNT_KEY = "cmVhbGtleQ=="  # base64 'realkey'
    return settings


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (2, 1), color="red").save(buffer, "PNG")
    return buffer.getvalue()


def test_local_uploads_never_default_to_the_working_directory():
    """xdist workers load a settings branch without MEDIA_ROOT; Django's ''
    default would write every local upload into the repository root."""
    media_root = django_settings.MEDIA_ROOT

    assert media_root
    assert Path(media_root).resolve() != Path.cwd().resolve()


def test_every_callable_storage_field_is_local():
    fields = [
        (model, field)
        for model in apps.get_models()
        for field in model._meta.get_fields()
        if isinstance(field, FileField) and hasattr(field, "_storage_callable")
    ]
    assert MeetupEvent._meta.get_field("image") in [field for _, field in fields]

    remote = [
        f"{model._meta.label}.{field.name}: {type(field.storage).__name__}"
        for model, field in fields
        if not isinstance(field.storage, FileSystemStorage)
    ]
    assert remote == []


@pytest.mark.parametrize(("factory", "azure_class"), FACTORIES)
def test_factory_stays_local_with_an_azure_account(azure_account, factory, azure_class):
    storage = factory()

    assert isinstance(storage, FileSystemStorage)
    assert not isinstance(storage, azure_class)


def test_cloned_field_stays_local_and_keeps_its_factory(azure_account):
    """Migration state clones fields, which calls the factory again."""
    clone = MeetupEvent._meta.get_field("image").clone()

    assert isinstance(clone.storage, FileSystemStorage)
    # Migrations still name the production factory, so nothing to migrate.
    assert clone.deconstruct()[3]["storage"] is get_crush_media_storage


def test_saving_a_media_field_never_builds_an_azure_client(azure_account, tmp_path):
    azure_account.MEDIA_ROOT = str(tmp_path)
    content = _png_bytes()
    event = MeetupEvent()

    # Fail fast: a mocked client would report every blob as existing, and
    # get_available_name() would then loop forever looking for a free name.
    with mock.patch.object(
        AzureStorage,
        "_get_service_client",
        side_effect=AssertionError("saving built an Azure client"),
    ) as build_client:
        event.image.save("banner.png", ContentFile(content), save=False)

    build_client.assert_not_called()
    assert isinstance(event.image.storage, FileSystemStorage)
    assert (tmp_path / event.image.name).read_bytes() == content


@pytest.mark.filterwarnings("ignore:datetime.datetime.utcnow:DeprecationWarning")
@pytest.mark.parametrize(("factory", "azure_class"), FACTORIES)
def test_factory_keeps_its_azure_backend_outside_pytest(
    azure_account, factory, azure_class
):
    """The switch is IS_TESTING alone; production still gets Azure."""
    azure_account.IS_TESTING = False

    with mock.patch.object(AzureStorage, "_get_service_client") as build_client:
        storage = factory()

    assert isinstance(storage, azure_class)
    assert storage.account_name == "realaccount"
    # Constructing the backend is offline; only blob I/O builds a client.
    build_client.assert_not_called()
