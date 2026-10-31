from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = "app"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("products/", views.product_list, name="product_list"),
    path("products/create/", views.product_create, name="product_create"),
    path("plans/", views.plan_list, name="plan_list"),
    path("plans/create/", views.plan_create, name="plan_create"),
    path("subscriptions/", views.subscription_list, name="subscription_list"),
    path("subscriptions/create/", views.subscription_create, name="subscription_create"),
    path("subscriptions/<int:pk>/", views.subscription_detail, name="subscription_detail"),
    path("subscriptions/<int:pk>/action/<str:action>/", views.subscription_transition, name="subscription_transition"),
    path("subscriptions/<int:subscription_id>/generate-invoice/", views.generate_invoice, name="generate_invoice"),
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("invoices/<int:pk>/confirm/", views.invoice_confirm, name="invoice_confirm"),
    path("invoices/<int:pk>/cancel/", views.invoice_cancel, name="invoice_cancel"),
    path("invoices/<int:invoice_id>/payments/add/", views.record_payment, name="record_payment"),
    path("reports/", views.reports, name="reports"),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]
