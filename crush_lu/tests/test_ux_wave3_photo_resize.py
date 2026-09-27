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

    def test_photo_upload_is_tracked_as_a_pending_upload(self):
        """[Codex review round 1, PR #1070] `photoUpload` lives in its own
        nested x-data scope, separate from `profileWizard`'s Continue
        button. Advancing used to be gated only on the wizard's own
        `isSaving`, which knew nothing about a resize+upload still running
        in the photo grid — a member who picked a large photo and tapped
        Continue immediately could advance to Review, and even unload the
        page, before the upload request was ever created. The resize/upload
        promise must be registered with the shared pending-upload tracker."""
        self.assertIn("trackPendingPhotoUpload(uploadPromise)", self.component_src)
        # The upload must actually be awaited, not fired-and-forgotten: the
        # resize .then() has to return the fetch chain rather than let the
        # outer promise resolve before the request completes.
        self.assertIn(
            'return fetch("/api/profile/draft/upload-photo/"', self.component_src
        )

    def test_wizard_step3_waits_for_pending_photo_uploads_before_advancing(self):
        wizard_start = self.src.index('Alpine.data("profileWizard"')
        step3_start = self.src.index("_completePhotoStep: function", wizard_start)
        step3_end = self.src.index("// Clear save error", step3_start)
        step3_src = self.src[step3_start:step3_end]
        self.assertIn("waitForPendingPhotoUploads()", step3_src)
        self.assertIn("_photoStepAdvancing = true", step3_src)
        self.assertIn("self._completePhotoStep(false)", self.src)

    def test_removing_a_photo_invalidates_its_pending_upload(self):
        """[Codex review round 2, PR #1070] A member who picks a large photo
        and taps Remove before the resize/upload settles used to have the
        delete request run first and the delayed upload run after — silently
        re-adding the just-removed photo. `_removePhoto` must bump a
        per-slot generation counter that `_handleFileSelect`'s resize/upload
        chain checks before it POSTs, and again before it applies the
        response, so a stale upload for a removed slot is abandoned."""
        select_start = self.component_src.index("_handleFileSelect: function")
        select_end = self.component_src.index("removePhoto1: function", select_start)
        select_src = self.component_src[select_start:select_end]
        remove_start = self.component_src.index("_removePhoto: function")
        remove_src = self.component_src[remove_start:]

        # The generation is captured before the resize starts...
        self.assertIn(
            "var generation = ++self.photos[index].uploadGeneration", select_src
        )
        # ...and checked before upload, after upload, and before an old
        # FileReader preview can overwrite a newer selection.
        self.assertEqual(
            select_src.count("self.photos[index].uploadGeneration !== generation"),
            3,
        )
        self.assertIn("++self.photos[index].uploadGeneration", remove_src)
        # Generation checks only guard client state. A started POST can still
        # write to the server, so the delete must queue behind that upload.
        self.assertIn("var previousOperation = slotOperations[index]", remove_src)
        self.assertIn("var deletePromise = previousOperation.then", remove_src)
        self.assertIn("slotOperations[index] = deletePromise.then", remove_src)

    def test_photo_step_drains_new_operations_and_locks_inputs(self):
        """Continue must wait for the live queue and reject new file picks."""
        self.assertIn(".then(waitForPendingPhotoUploads)", self.src)
        self.assertIn("if (_photoStepAdvancing)", self.component_src)
        template = (Path(__file__).parents[1] / "templates/crush_lu/create_profile.html").read_text(encoding="utf-8")
        for number in (1, 2, 3):
            self.assertIn(
                f'id="photo{number}" accept="image/*" class="hidden"\n'
                '                               x-bind:disabled="isSavingStep"',
                template,
            )

    def test_successful_auto_upload_replaces_the_file_input_with_the_resized_copy(
        self,
    ):
        """[Codex review round 2, PR #1070] After the auto-upload succeeds,
        the native `photo_N` file input still held the original, full-size
        `File` — the final non-JS `form.submit()` in `handleFormSubmit`
        therefore re-sent the original (defeating the resize, and able to
        exceed the form's 10 MB limit even though the resized copy already
        saved fine). The success branch must swap the input's FileList for
        the resized file via DataTransfer."""
        select_start = self.component_src.index("_handleFileSelect: function")
        select_end = self.component_src.index("removePhoto1: function", select_start)
        select_src = self.component_src[select_start:select_end]
        success_start = select_src.index("if (result.success)")
        success_end = select_src.index("} else {", success_start)
        success_src = select_src[success_start:success_end]
        self.assertIn(
            'document.getElementById(\n                                        "photo" + photoNumber',
            select_src,
        )
        self.assertIn("new DataTransfer()", success_src)
        self.assertIn("dt.items.add(uploadFile)", success_src)
        self.assertIn("input.files = dt.files", success_src)
