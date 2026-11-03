"""Replace ``delete`` on the storage behind the profile photo fields.

Not collected by pytest (the name does not match ``test_*.py``).

All three photo fields share one storage object. Under pytest-xdist it is
``default_storage``, a ``LazyObject``: the workers inherit the
``AZURE_ACCOUNT_NAME`` that conftest.py sets, so they load the settings branch
that defines no ``crush_private`` alias and ``get_crush_photo_storage`` falls
back to it. A single-process run imports settings before that hook runs and
gets a plain ``FileSystemStorage``.

Since pytest 9.2, ``monkeypatch.setattr`` takes the value to restore from the
target's own ``__dict__``. A proxy's ``__dict__`` never holds ``delete``, so
every patch through it is recorded as "absent" and teardown deletes the
attribute once per patch: the second ``delattr`` raises. Patching the concrete
storage once restores cleanly, however many patches a test stacks on it.
"""

from django.utils.functional import LazyObject, empty

from crush_lu.models import CrushProfile

PHOTO_FIELDS = ("photo_1", "photo_2", "photo_3")


def stub_photo_deletes(monkeypatch, replacement):
    """Route every photo field's ``storage.delete`` to ``replacement``."""
    storages = {}
    for field in PHOTO_FIELDS:
        storage = CrushProfile._meta.get_field(field).storage
        if isinstance(storage, LazyObject):
            if storage._wrapped is empty:
                storage._setup()
            storage = storage._wrapped
        storages[id(storage)] = storage
    for storage in storages.values():
        monkeypatch.setattr(storage, "delete", replacement)
