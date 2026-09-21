from django.utils.deprecation import MiddlewareMixin

from .services.leads import capture_attribution


class LeadAttributionMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if (
            request.method == "GET"
            and match
            and match.app_name == "arborist"
            and match.url_name
            not in {
                "lead_detail",
                "lead_photo",
                "booking_success",
                "api_calculate_zone",
            }
        ):
            capture_attribution(request)
