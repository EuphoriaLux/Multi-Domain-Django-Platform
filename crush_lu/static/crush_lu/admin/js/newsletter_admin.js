/**
 * Newsletter admin JS: show/hide segment_key field based on audience selection.
 */
(function () {
    'use strict';

    function toggleSegmentField() {
        var audienceSelect = document.getElementById('id_audience');
        var segmentRow = document.querySelector('.field-segment_key');
        if (!audienceSelect || !segmentRow) return;

        if (audienceSelect.value === 'segment') {
            segmentRow.style.display = '';
        } else {
            segmentRow.style.display = 'none';
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        var audienceSelect = document.getElementById('id_audience');
        if (audienceSelect) {
            audienceSelect.addEventListener('change', toggleSegmentField);
            toggleSegmentField();
        }
    });
})();
