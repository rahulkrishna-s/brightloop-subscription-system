from django.contrib import admin

# Register your models here.

from django.contrib import admin
from .models import (
    UserProfile,
    TaxRate,
    Product,
    ProductVariant,
    SubscriptionPlan,
    QuotationTemplate,
    QuotationTemplateLine,
    DiscountRule,
    Subscription,
    SubscriptionLine,
    Invoice,
    InvoiceLine,
    Payment,
)

admin.site.register([
    UserProfile,
    TaxRate,
    Product,
    ProductVariant,
    SubscriptionPlan,
    QuotationTemplate,
    QuotationTemplateLine,
    DiscountRule,
    Subscription,
    SubscriptionLine,
    Invoice,
    InvoiceLine,
    Payment,
])