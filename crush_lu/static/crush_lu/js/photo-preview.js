/**
 * Photo Preview Component
 * Handles image preview for file upload inputs
 */

/**
 * Preview an image file before upload
 * @param {HTMLInputElement} input - The file input element
 * @param {string} previewId - The ID of the preview element to update
 */
function previewImage(input, previewId) {
    const preview = document.getElementById(previewId);
    if (!preview) return;

    const container = preview.parentElement;

    if (input.files && input.files[0]) {
        const reader = new FileReader();

        reader.onload = function(e) {
            // Replace placeholder or existing image with new preview
            if (preview.tagName === 'DIV') {
                // Currently showing placeholder, replace with image
                const img = document.createElement('img');
                img.src = e.target.result;
                img.alt = 'Preview';
                img.className = 'photo-preview';
                img.id = previewId;
                container.innerHTML = '';
                container.appendChild(img);

                // Add "New" badge
                const badge = document.createElement('span');
                badge.className = 'photo-badge photo-badge--new';
                badge.textContent = 'New';
                container.appendChild(badge);
            } else {
                // Already showing an image, update it
                preview.src = e.target.result;

                // Update or create "New" badge
                let badge = container.querySelector('.photo-badge');
                if (badge) {
                    badge.textContent = 'New';
                    badge.classList.add('photo-badge--new');
                } else {
                    badge = document.createElement('span');
                    badge.className = 'photo-badge photo-badge--new';
                    badge.textContent = 'New';
                    container.appendChild(badge);
                }
            }
        };

        reader.readAsDataURL(input.files[0]);
    }
}

/**
 * Initialize photo preview for a single file input by ID
 * @param {string} inputId - The ID of the file input element
 * @param {string} previewId - The ID of the preview element
 */
function initPhotoPreview(inputId, previewId) {
    const input = document.getElementById(inputId);
    if (input) {
        input.addEventListener('change', function() {
            previewImage(this, previewId);
        });
    }
}

/**
 * Initialize all photo previews on the page
 * Called on page load and after HTMX swaps
 */
function initAllPhotoPreviews() {
    // Initialize for coach profile photo
    initPhotoPreview('id_photo', 'photo-preview');

    // Initialize for profile photos (photo_1, photo_2, photo_3)
    initPhotoPreview('id_photo_1', 'preview-photo-1');
    initPhotoPreview('id_photo_2', 'preview-photo-2');
    initPhotoPreview('id_photo_3', 'preview-photo-3');
}

// Auto-initialize on DOM ready for common patterns
document.addEventListener('DOMContentLoaded', initAllPhotoPreviews);

// Reinitialize after HTMX swaps (for dynamic content updates)
document.body.addEventListener('htmx:afterSwap', function(event) {
    // Check if the swap target contains photo inputs
    const target = event.detail.target;
    if (target && (
        target.querySelector('[id^="id_photo"]') ||
        target.id === 'form-container' ||
        target.id.startsWith('photo-card-')
    )) {
        // Small delay to ensure DOM is fully updated
        setTimeout(initAllPhotoPreviews, 50);
    }
});