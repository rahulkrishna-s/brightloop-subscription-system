from django.db import models

# Create your models here.

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import (
    MinValueValidator,
    MaxValueValidator,
)
from django.db import models
from django.db.models import Q
from django.utils import timezone


# ============================================================
# 1. USER PROFILES AND ROLES
# Uses Django's built-in User model for authentication.
# ============================================================

class UserProfile(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        STAFF = "staff", "Internal Staff"
        CUSTOMER = "customer", "Customer"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.CUSTOMER,
        db_index=True,
    )
    phone = models.CharField(max_length=20, blank=True)
    company_name = models.CharField(max_length=200, blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"


# ============================================================
# 2. PRODUCTS, VARIANTS AND TAXES
# ============================================================

class TaxRate(models.Model):
    name = models.CharField(max_length=100)
    rate = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        validators=[
            MinValueValidator(Decimal("0")),
            MaxValueValidator(Decimal("100")),
        ],
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "rate"],
                name="unique_tax_name_rate",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.rate}%)"


class Product(models.Model):
    class ProductType(models.TextChoices):
        SOFTWARE = "software", "Software"
        SERVICE = "service", "Service"
        ADDON = "addon", "Add-on"
        PHYSICAL = "physical", "Physical Product"
        OTHER = "other", "Other"

    name = models.CharField(max_length=200)
    product_type = models.CharField(
        max_length=20,
        choices=ProductType.choices,
        default=ProductType.SOFTWARE,
    )
    description = models.TextField(blank=True)
    sales_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    cost_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    taxes = models.ManyToManyField(
        TaxRate,
        blank=True,
        related_name="products",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_active", "product_type"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(sales_price__gte=0),
                name="product_sales_price_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(cost_price__gte=0),
                name="product_cost_price_nonnegative",
            ),
        ]

    def __str__(self):
        return self.name


class ProductVariant(models.Model):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    attribute_name = models.CharField(
        max_length=100,
        help_text="For example: Brand, Edition or Storage",
    )
    value = models.CharField(
        max_length=100,
        help_text="For example: Odoo, Premium or 1 TB",
    )
    price_adjustment = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["product", "attribute_name", "value"],
                name="unique_product_variant_value",
            )
        ]

    @property
    def effective_price(self):
        return self.product.sales_price + self.price_adjustment

    def __str__(self):
        return f"{self.product.name}: {self.attribute_name}={self.value}"


# ============================================================
# 3. SUBSCRIPTION PLANS
# ============================================================

class SubscriptionPlan(models.Model):
    class BillingInterval(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    products = models.ManyToManyField(
        Product,
        related_name="plans",
        blank=True,
    )
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    billing_interval = models.CharField(
        max_length=10,
        choices=BillingInterval.choices,
        default=BillingInterval.MONTHLY,
    )
    minimum_quantity = models.PositiveIntegerField(default=1)
    available_from = models.DateField(null=True, blank=True)
    available_until = models.DateField(null=True, blank=True)

    auto_close_on_expiry = models.BooleanField(default=True)
    allow_early_close = models.BooleanField(default=True)
    allow_pause = models.BooleanField(default=True)
    allow_renewal = models.BooleanField(default=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(price__gte=0),
                name="plan_price_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(minimum_quantity__gte=1),
                name="plan_min_quantity_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(available_from__isnull=True)
                    | Q(available_until__isnull=True)
                    | Q(available_until__gte=models.F("available_from"))
                ),
                name="plan_valid_date_range",
            ),
        ]

    def clean(self):
        if (
            self.available_from
            and self.available_until
            and self.available_until < self.available_from
        ):
            raise ValidationError(
                {"available_until": "End date cannot precede start date."}
            )

    def __str__(self):
        return f"{self.name} - {self.price}/{self.billing_interval}"


# ============================================================
# 4. QUOTATION TEMPLATES
# ============================================================

class QuotationTemplate(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    validity_days = models.PositiveIntegerField(default=30)
    default_plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotation_templates",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class QuotationTemplateLine(models.Model):
    template = models.ForeignKey(
        QuotationTemplate,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
    )
    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )

    def __str__(self):
        return f"{self.template.name}: {self.product.name}"


# ============================================================
# 5. DISCOUNT RULES
# ============================================================

class DiscountRule(models.Model):
    class DiscountType(models.TextChoices):
        FIXED = "fixed", "Fixed Amount"
        PERCENTAGE = "percentage", "Percentage"

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    discount_type = models.CharField(
        max_length=12,
        choices=DiscountType.choices,
    )
    value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    minimum_purchase = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    minimum_quantity = models.PositiveIntegerField(default=1)

    valid_from = models.DateField()
    valid_until = models.DateField()

    usage_limit = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Leave blank for unlimited uses.",
    )
    usage_count = models.PositiveIntegerField(default=0)

    products = models.ManyToManyField(
        Product,
        blank=True,
        related_name="discount_rules",
    )
    applies_to_subscription = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=Q(value__gte=0),
                name="discount_value_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(minimum_purchase__gte=0),
                name="discount_min_purchase_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(minimum_quantity__gte=1),
                name="discount_min_quantity_positive",
            ),
            models.CheckConstraint(
                condition=Q(valid_until__gte=models.F("valid_from")),
                name="discount_valid_date_range",
            ),
            models.CheckConstraint(
                condition=(
                    Q(usage_limit__isnull=True)
                    | Q(usage_limit__gte=models.F("usage_count"))
                ),
                name="discount_usage_within_limit",
            ),
            models.CheckConstraint(
                condition=(
                    Q(discount_type="fixed")
                    | (
                        Q(discount_type="percentage")
                        & Q(value__lte=100)
                    )
                ),
                name="discount_percentage_at_most_100",
            ),
        ]

    def clean(self):
        if self.valid_until < self.valid_from:
            raise ValidationError(
                {"valid_until": "End date cannot precede start date."}
            )
        if (
            self.discount_type == self.DiscountType.PERCENTAGE
            and self.value > 100
        ):
            raise ValidationError(
                {"value": "Percentage discount cannot exceed 100."}
            )

    def __str__(self):
        return self.name


# ============================================================
# 6. SUBSCRIPTIONS AND SUBSCRIPTION LINES
# ============================================================

class Subscription(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        QUOTATION = "quotation", "Quotation"
        CONFIRMED = "confirmed", "Confirmed"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        CLOSED = "closed", "Closed"

    number = models.CharField(
        max_length=30,
        unique=True,
        help_text="Generate a unique subscription number in the service/view.",
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="subscriptions",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="subscriptions",
    )
    quotation_template = models.ForeignKey(
        QuotationTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscriptions",
    )
    discount_rule = models.ForeignKey(
        DiscountRule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscriptions",
    )

    start_date = models.DateField()
    expiry_date = models.DateField(null=True, blank=True)
    next_billing_date = models.DateField(null=True, blank=True)
    payment_terms = models.CharField(max_length=200, blank=True)

    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    quotation_valid_until = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_subscriptions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "next_billing_date"]),
            models.Index(fields=["customer", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(expiry_date__isnull=True)
                    | Q(expiry_date__gte=models.F("start_date"))
                ),
                name="subscription_valid_date_range",
            )
        ]

    def clean(self):
        if (
            self.expiry_date
            and self.expiry_date < self.start_date
        ):
            raise ValidationError(
                {"expiry_date": "Expiry cannot precede the start date."}
            )

    def __str__(self):
        return f"{self.number} - {self.customer}"


class SubscriptionLine(models.Model):
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="subscription_lines",
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="subscription_lines",
    )
    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    description = models.CharField(max_length=255, blank=True)

    @property
    def subtotal(self):
        return self.quantity * self.unit_price

    def clean(self):
        if self.variant_id and self.product_id:
            if self.variant.product_id != self.product_id:
                raise ValidationError(
                    {"variant": "Variant must belong to the selected product."}
                )

    def __str__(self):
        return f"{self.product.name} x {self.quantity}"


# ============================================================
# 7. INVOICES AND INVOICE LINES
# ============================================================

class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        SENT = "sent", "Sent"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=30, unique=True)
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.PROTECT,
        related_name="invoices",
    )

    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField()
    billing_period_start = models.DateField()
    billing_period_end = models.DateField()

    subtotal = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    discount_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    tax_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    total_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )

    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        indexes = [
            models.Index(fields=["status", "due_date"]),
            models.Index(fields=["customer", "status"]),
            models.Index(fields=["subscription", "billing_period_start"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "subscription",
                    "billing_period_start",
                    "billing_period_end",
                ],
                condition=~Q(status="cancelled"),
                name="unique_active_subscription_billing_period",
            ),
            models.CheckConstraint(
                condition=Q(due_date__gte=models.F("issue_date")),
                name="invoice_due_after_issue",
            ),
            models.CheckConstraint(
                condition=Q(billing_period_end__gte=models.F("billing_period_start")),
                name="invoice_valid_billing_period",
            ),
            models.CheckConstraint(
                condition=Q(subtotal__gte=0),
                name="invoice_subtotal_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(discount_amount__gte=0),
                name="invoice_discount_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(tax_amount__gte=0),
                name="invoice_tax_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(total_amount__gte=0),
                name="invoice_total_nonnegative",
            ),
        ]

    @property
    def amount_paid(self):
        return self.payments.filter(
            status=Payment.Status.RECORDED
        ).aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")

    @property
    def outstanding_amount(self):
        return max(
            self.total_amount - self.amount_paid,
            Decimal("0.00"),
        )

    @property
    def is_overdue(self):
        return (
            self.status not in [
                self.Status.PAID,
                self.Status.CANCELLED,
                self.Status.DRAFT,
            ]
            and self.due_date < timezone.localdate()
            and self.outstanding_amount > 0
        )

    def __str__(self):
        return self.number


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="invoice_lines",
    )
    description = models.CharField(max_length=255, blank=True)
    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    # Snapshot the applicable combined tax percentage at invoice time.
    tax_rate = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal("0"),
        validators=[
            MinValueValidator(Decimal("0")),
            MaxValueValidator(Decimal("100")),
        ],
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )

    @property
    def subtotal(self):
        return self.quantity * self.unit_price

    @property
    def taxable_amount(self):
        return max(self.subtotal - self.discount_amount, Decimal("0.00"))

    @property
    def tax_amount(self):
        return (
            self.taxable_amount * self.tax_rate / Decimal("100")
        ).quantize(Decimal("0.01"))

    @property
    def total(self):
        return self.taxable_amount + self.tax_amount

    def clean(self):
        if self.discount_amount > self.subtotal:
            raise ValidationError(
                {"discount_amount": "Discount cannot exceed the line subtotal."}
            )

    def __str__(self):
        return f"{self.invoice.number}: {self.product.name}"


# ============================================================
# 8. PAYMENTS
# ============================================================

class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        CARD = "card", "Card"
        UPI = "upi", "UPI"
        CHEQUE = "cheque", "Cheque"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        RECORDED = "recorded", "Recorded"
        REVERSED = "reversed", "Reversed"

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    method = models.CharField(
        max_length=20,
        choices=Method.choices,
        default=Method.BANK_TRANSFER,
    )
    payment_date = models.DateField(default=timezone.localdate)
    reference = models.CharField(max_length=100, blank=True)
    note = models.TextField(blank=True)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.RECORDED,
        db_index=True,
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_payments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-id"]
        indexes = [
            models.Index(fields=["payment_date", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="payment_amount_positive",
            )
        ]

    def __str__(self):
        return f"{self.invoice.number}: {self.amount} ({self.method})"
