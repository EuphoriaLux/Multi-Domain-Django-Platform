"""Tests for UX Wave 3 · WP5 (finding 3-14, scoped part) — client-side photo
downscale/re-encode and alert() removal in the profile-photo upload path.

`photoUpload` in alpine-components.js is plain JS with no Python-visible
behaviour, so these are structural source assertions (the same pattern
`test_coach_unverified_profiles.LockOrderInvariantTests` uses for lock
ordering) rather than executing the component. They fail against
`origin/main`, where the upload handler posts the raw `File` straight to
`/api/profile/draft/upload-photo/` and reports failures with `alert(...)`.
"""

from pathlib import Path

from django.test import SimpleTestCase

JS_PATH = (
    Path(__file__).resolve().parent.parent
    / "static"
    / "crush_lu"
    / "js"
    / "alpine-components.js"
)


class PhotoUploadResizeAndToastTests(SimpleTestCase):
    def setUp(self):
        self.src = JS_PATH.read_text(encoding="utf-8")
        # Isolate the photoUpload component body so assertions can't
        # accidentally match some unrelated part of this 15k-line file.
        start = self.src.index('Alpine.data("photoUpload"')
        end = self.src.index('Alpine.data("profileWizard"')
        self.component_src = self.src[start:end]

    def test_resize_helper_exists_and_is_used_before_upload(self):
        self.assertIn("function resizeImageForUpload(", self.src)
        self.assertIn("resizeImageForUpload(file, 2048, 0.85)", self.component_src)

    def test_resize_helper_respects_exif_orientation(self):
        # createImageBitmap({imageOrientation: 'from-image'}) is what bakes
        # EXIF rotation into the pixels — drawImage from a raw <img> does not.
        helper_start = self.src.index("function resizeImageForUpload(")
        helper_end = self.src.index("function notifyError(")
        helper_src = self.src[helper_start:helper_end]
        self.assertIn("imageOrientation", helper_src)
        self.assertIn("from-image", helper_src)

    def test_photo_upload_component_no_longer_calls_alert_directly(self):
        self.assertNotIn("alert(", self.component_src)

    def test_photo_upload_component_routes_errors_through_notify_error(self):
        self.assertEqual(self.component_src.count("notifyError("), 4)

    def test_notify_error_prefers_the_toast_store_over_alert(self):
        helper_start = self.src.index("function notifyError(")
        helper_end = self.src.index('Alpine.data("photoUpload"')
        helper_src = self.src[helper_start:helper_end]
        self.assertIn('Alpine.store("toasts")', helper_src)
        self.assertIn(".add({", helper_src)
