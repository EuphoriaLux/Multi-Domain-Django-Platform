"""Read-only accounting endpoints backing the CRM's ``/accounting`` page.

The SPA fetches the four ledgers as flat lists and computes every summary
metric (VAT, cashflow, month-over-month deltas) client-side, so these views
stay list-only. Rows are entered through the Django admin.
"""

from rest_framework import generics
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models import PaymentIn, PaymentOut, Payroll, Refund
from .serializers import (
    PaymentInSerializer,
    PaymentOutSerializer,
    PayrollSerializer,
    RefundSerializer,
)


class FinanceListView(generics.ListAPIView):
    """Staff-only list endpoint using the hub's ``{"items": [...]}`` envelope."""

    permission_classes = [IsAdminUser]

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response({"items": serializer.data})


class PaymentsInView(FinanceListView):
    serializer_class = PaymentInSerializer
    queryset = PaymentIn.objects.all()


class PaymentsOutView(FinanceListView):
    serializer_class = PaymentOutSerializer
    # ``locationId`` is serialized from the local ``location_id`` column, so
    # listing venue costs needs no join.
    queryset = PaymentOut.objects.all()


class PayrollView(FinanceListView):
    serializer_class = PayrollSerializer
    queryset = Payroll.objects.all()


class RefundsView(FinanceListView):
    serializer_class = RefundSerializer
    queryset = Refund.objects.all()
