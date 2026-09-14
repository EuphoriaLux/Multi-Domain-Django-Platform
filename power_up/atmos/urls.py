from django.urls import path

from . import views

app_name = "atmos"

urlpatterns = [
    # Guest zone — anonymous, cookie-scoped (spec §6.1)
    path("t/<str:qr_token>/", views.guest_scan, name="guest_scan"),
    path("join/", views.guest_join, name="guest_join"),
    path("menu/", views.guest_menu, name="guest_menu"),
    path("cart/add/", views.cart_add, name="cart_add"),
    path("cart/update/", views.cart_update, name="cart_update"),
    path("cart/", views.cart_detail, name="cart_detail"),
    path("order/place/", views.order_place, name="order_place"),
    path("order/<uuid:pk>/", views.order_status, name="order_status"),

    # Staff zone — @staff_member_required (spec §6.2)
    path("staff/", views.staff_home, name="staff_home"),
    path("staff/<slug:venue_slug>/kds/", views.kds, name="kds"),
    path("staff/order/<uuid:pk>/status/", views.order_set_status, name="order_set_status"),
    path("staff/order/<uuid:pk>/print/", views.order_print_payload, name="order_print_payload"),
]
