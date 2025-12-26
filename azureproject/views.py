"""
Views for azureproject (shared across all domains).

Contains:
- CSP violation report endpoint
"""
import json
import logging
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

# Separate logger for CSP reports to allow independent logging level
logger = logging.getLogger('csp_reports')


@csrf_exempt  # CSP reports are sent by the browser without CSRF tokens
@require_POST
def csp_report(request):
    """
    Receive and log Content Security Policy violation reports.

    Browsers send CSP violation reports as JSON to the report-uri endpoint.
    This view logs the violations for analysis.

    Request body format (browser-generated):
    {
        "csp-report": {
            "document-uri": "https://example.com/page",
            "violated-directive": "script-src 'self'",
            "blocked-uri": "https://evil.com/bad.js",
            "original-policy": "...",
            ...
        }
    }

    Returns:
        HTTP 204 No Content (success, no response body needed)
    """
    try:
        # Parse the CSP report JSON
        report = json.loads(request.body.decode('utf-8'))
        csp_report_data = report.get('csp-report', {})

        # Extract key fields for logging
        blocked_uri = csp_report_data.get('blocked-uri', 'unknown')
        violated_directive = csp_report_data.get('violated-directive', 'unknown')
        document_uri = csp_report_data.get('document-uri', 'unknown')
        source_file = csp_report_data.get('source-file', 'unknown')
        line_number = csp_report_data.get('line-number', 'unknown')

        # Log the violation with key details
        logger.warning(
            f"CSP Violation: blocked={blocked_uri}, "
            f"directive={violated_directive}, "
            f"page={document_uri}, "
            f"source={source_file}:{line_number}"
        )

        # Log full report at debug level for detailed analysis
        logger.debug(f"CSP Full Report: {json.dumps(report, indent=2)}")

    except json.JSONDecodeError as e:
        logger.error(f"CSP Report: Invalid JSON received: {e}")
    except Exception as e:
        logger.error(f"CSP Report: Error processing report: {e}")

    # Always return 204 - browser expects no content
    return HttpResponse(status=204)
