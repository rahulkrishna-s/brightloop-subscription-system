
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .models import (
    UserProfile,
    Product,
    ProductVariant,
    SubscriptionPlan,
    Subscription,
    SubscriptionLine,
    Invoice,
    InvoiceLine,
    Payment,
    TaxRate,
)

User = get_user_model()


# =========================================================
# COMMON HELPERS AND ROLE PERMISSIONS
# =========================================================

def get_role(user):
    if user.is_superuser:
        return "admin"

    profile = UserProfile.objects.filter(user=user).first()
    return profile.role if profile else None


def require_roles(*roles):
    def decorator(view_func):
        @login_required
        def wrapper(request, *args, **kwargs):
            if get_role(request.user) not in roles:
                raise PermissionDenied(
                    "You do not have permission to access this page."
                )
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def generate_number(prefix):
    return f"{prefix}-{timezone.now():%Y%m%d}-{uuid4().hex[:8].upper()}"


def positive_decimal(value, label="Amount"):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError(f"{label} must be a valid number.")

    if not number.is_finite() or number <= 0:
        raise ValidationError(f"{label} must be greater than zero.")

    return number.quantize(Decimal("0.01"))


def positive_integer(value, label="Quantity"):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{label} must be a whole number.")

    if number < 1:
        raise ValidationError(f"{label} must be at least 1.")

    return number


def subscription_queryset(user):
    qs = Subscription.objects.select_related(
        "customer", "plan"
    ).prefetch_related("lines__product")

    role = get_role(user)
    if role == "customer":
        return qs.filter(customer=user)
    if role in {"admin", "staff"}:
        return qs
    raise PermissionDenied


def invoice_queryset(user):
    qs = Invoice.objects.select_related(
        "customer", "subscription"
    ).prefetch_related("lines", "payments")

    role = get_role(user)
    if role == "customer":
        return qs.filter(customer=user)
    if role in {"admin", "staff"}:
        return qs
    raise PermissionDenied


# =========================================================
# DASHBOARD
# =========================================================

@login_required
def dashboard(request):
    role = get_role(request.user)

    if role not in {"admin", "staff", "customer"}:
        raise PermissionDenied

    if role == "customer":
        subscriptions = Subscription.objects.filter(
            customer=request.user
        )
        invoices = Invoice.objects.filter(customer=request.user)
        payments = Payment.objects.filter(
            invoice__customer=request.user,
            status="recorded",
        )
    else:
        subscriptions = Subscription.objects.all()
        invoices = Invoice.objects.all()
        payments = Payment.objects.filter(status="recorded")

    today = timezone.localdate()

    context = {
        "role": role,
        "total_products": Product.objects.filter(active=True).count(),
        "total_plans": SubscriptionPlan.objects.count(),
        "active_subscriptions": subscriptions.filter(status="active").count(),
        "total_subscriptions": subscriptions.count(),
        "total_invoices": invoices.exclude(status="cancelled").count(),
        "overdue_invoices": invoices.filter(
            due_date__lt=today
        ).exclude(status__in=["paid", "cancelled"]).count(),
        "revenue_received": payments.aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0.00"),
        "recent_subscriptions": subscriptions.order_by("-created_at")[:5],
        "recent_invoices": invoices.order_by("-created_at")[:5],
    }

    return render(request, "dashboard.html", context)


# =========================================================
# PRODUCTS
# =========================================================

@login_required
def product_list(request):
    products = Product.objects.filter(active=True).prefetch_related("taxes")
    search = request.GET.get("q", "").strip()

    if search:
        products = products.filter(
            Q(name__icontains=search)
            | Q(description__icontains=search)
        )

    page_obj = Paginator(
        products.order_by("name"), 12
    ).get_page(request.GET.get("page"))

    return render(request, "products/product_list.html", {
        "page_obj": page_obj,
        "search": search,
    })


@require_roles("admin")
def product_create(request):
    if request.method == "POST":
        try:
            name = request.POST.get("name", "").strip()
            product_type = request.POST.get("product_type", "").strip()
            description = request.POST.get("description", "").strip()

            if not name:
                raise ValidationError("Product name is required.")

            sales_price = positive_decimal(
                request.POST.get("sales_price"), "Sales price"
            )

            try:
                cost_price = Decimal(request.POST.get("cost_price", "0"))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError("Enter a valid cost price.")

            if not cost_price.is_finite() or cost_price < 0:
                raise ValidationError("Cost price cannot be negative.")

            with transaction.atomic():
                product = Product.objects.create(
                    name=name,
                    product_type=product_type,
                    description=description,
                    sales_price=sales_price,
                    cost_price=cost_price,
                )

                product.taxes.set(
                    TaxRate.objects.filter(
                        id__in=request.POST.getlist("taxes"),
                        active=True,
                    )
                )

            messages.success(request, "Product created successfully.")
            return redirect("product_list")

        except (ValidationError, InvalidOperation, ValueError) as exc:
            message = (
                "; ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            messages.error(request, message)

    return render(request, "products/product_form.html", {
        "taxes": TaxRate.objects.filter(active=True),
        "product_types": Product.TYPE_CHOICES,
    })


# =========================================================
# PLANS
# =========================================================

@login_required
def plan_list(request):
    plans = SubscriptionPlan.objects.prefetch_related("products")

    if get_role(request.user) == "customer":
        today = timezone.localdate()
        plans = plans.filter(
            Q(available_from__isnull=True)
            | Q(available_from__lte=today),
            Q(available_until__isnull=True)
            | Q(available_until__gte=today),
        )

    page_obj = Paginator(
        plans.order_by("price"), 12
    ).get_page(request.GET.get("page"))

    return render(request, "plans/plan_list.html", {
        "page_obj": page_obj,
    })


@require_roles("admin")
def plan_create(request):
    if request.method == "POST":
        try:
            name = request.POST.get("name", "").strip()
            if not name:
                raise ValidationError("Plan name is required.")

            price = positive_decimal(
                request.POST.get("price"), "Plan price"
            )
            minimum_quantity = positive_integer(
                request.POST.get("minimum_quantity", "1"),
                "Minimum quantity",
            )

            interval = request.POST.get("billing_interval", "")
            valid_intervals = {
                choice[0]
                for choice in SubscriptionPlan.BILLING_INTERVAL_CHOICES
            }

            if interval not in valid_intervals:
                raise ValidationError("Select a valid billing interval.")

            available_from = parse_date(
                request.POST.get("available_from", "")
            ) if request.POST.get("available_from") else None

            available_until = parse_date(
                request.POST.get("available_until", "")
            ) if request.POST.get("available_until") else None

            if (
                available_from and available_until
                and available_until < available_from
            ):
                raise ValidationError("Invalid plan availability dates.")

            with transaction.atomic():
                plan = SubscriptionPlan.objects.create(
                    name=name,
                    description=request.POST.get("description", "").strip(),
                    price=price,
                    billing_interval=interval,
                    minimum_quantity=minimum_quantity,
                    available_from=available_from,
                    available_until=available_until,
                    auto_close=request.POST.get("auto_close") == "on",
                    allow_early_close=(
                        request.POST.get("allow_early_close") == "on"
                    ),
                    allow_pause=request.POST.get("allow_pause") == "on",
                    allow_renewal=request.POST.get("allow_renewal") == "on",
                )

                plan.products.set(
                    Product.objects.filter(
                        id__in=request.POST.getlist("products"),
                        active=True,
                    )
                )

            messages.success(request, "Plan created successfully.")
            return redirect("plan_list")

        except (ValidationError, InvalidOperation, ValueError) as exc:
            message = (
                "; ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            messages.error(request, message)

    return render(request, "plans/plan_form.html", {
        "products": Product.objects.filter(active=True),
        "billing_intervals": SubscriptionPlan.BILLING_INTERVAL_CHOICES,
    })


# =========================================================
# SUBSCRIPTIONS
# =========================================================

@login_required
def subscription_list(request):
    subscriptions = subscription_queryset(request.user)

    status = request.GET.get("status", "")
    search = request.GET.get("q", "").strip()

    valid_statuses = {
        choice[0] for choice in Subscription.STATUS_CHOICES
    }
    if status in valid_statuses:
        subscriptions = subscriptions.filter(status=status)

    if search:
        subscriptions = subscriptions.filter(
            Q(subscription_number__icontains=search)
            | Q(plan__name__icontains=search)
            | Q(customer__email__icontains=search)
        )

    page_obj = Paginator(
        subscriptions.order_by("-created_at"), 15
    ).get_page(request.GET.get("page"))

    return render(request, "subscriptions/subscription_list.html", {
        "page_obj": page_obj,
        "search": search,
        "selected_status": status,
        "statuses": Subscription.STATUS_CHOICES,
    })


@login_required
def subscription_create(request):
    role = get_role(request.user)
    if role not in {"admin", "staff", "customer"}:
        raise PermissionDenied

    today = timezone.localdate()
    plans = SubscriptionPlan.objects.filter(
        Q(available_from__isnull=True)
        | Q(available_from__lte=today),
        Q(available_until__isnull=True)
        | Q(available_until__gte=today),
    )

    if request.method == "POST":
        try:
            plan = get_object_or_404(
                plans, pk=request.POST.get("plan_id")
            )
            quantity = positive_integer(
                request.POST.get("quantity", "1")
            )

            if quantity < plan.minimum_quantity:
                raise ValidationError(
                    f"Minimum quantity is {plan.minimum_quantity}."
                )

            start_date = parse_date(request.POST.get("start_date", ""))
            if not start_date or start_date < today:
                raise ValidationError(
                    "Enter a valid start date that is today or later."
                )

            if role == "customer":
                customer = request.user
            else:
                customer = get_object_or_404(
                    User, pk=request.POST.get("customer_id")
                )

            product = get_object_or_404(
                plan.products.filter(active=True),
                pk=request.POST.get("product_id"),
            )

            variant = None
            if request.POST.get("variant_id"):
                variant = get_object_or_404(
                    ProductVariant,
                    pk=request.POST.get("variant_id"),
                    product=product,
                )

            unit_price = product.sales_price
            if variant:
                unit_price += variant.price_adjustment

            if unit_price < 0:
                raise ValidationError("Invalid product price.")

            with transaction.atomic():
                subscription = Subscription.objects.create(
                    subscription_number=generate_number("SUB"),
                    customer=customer,
                    plan=plan,
                    start_date=start_date,
                    next_billing_date=start_date,
                    payment_terms=request.POST.get(
                        "payment_terms", "Due on receipt"
                    ).strip()[:100],
                    status="draft",
                    created_by=request.user,
                )

                SubscriptionLine.objects.create(
                    subscription=subscription,
                    product=product,
                    variant=variant,
                    quantity=quantity,
                    unit_price=unit_price,
                    description=product.name,
                )

            messages.success(request, "Subscription created as a draft.")
            return redirect(
                "subscription_detail", pk=subscription.pk
            )

        except (ValidationError, InvalidOperation, ValueError) as exc:
            message = (
                "; ".join(exc.messages)
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            messages.error(request, message)

    return render(request, "subscriptions/subscription_form.html", {
        "plans": plans.prefetch_related("products"),
        "customers": User.objects.filter(
            is_active=True,
            is_staff=False,
            is_superuser=False,
        ) if role in {"admin", "staff"} else None,
    })


@login_required
def subscription_detail(request, pk):
    subscription = get_object_or_404(
        subscription_queryset(request.user), pk=pk
    )
    invoices = invoice_queryset(request.user).filter(
        subscription=subscription
    ).order_by("-created_at")

    return render(request, "subscriptions/subscription_detail.html", {
        "subscription": subscription,
        "invoices": invoices,
    })


@require_POST
@require_roles("admin", "staff")
def subscription_transition(request, pk, action):
    transitions = {
        "quote": ("draft", "quotation"),
        "confirm": ("quotation", "confirmed"),
        "activate": ("confirmed", "active"),
        "pause": ("active", "paused"),
        "resume": ("paused", "active"),
        "close": ("active", "closed"),
    }

    if action not in transitions:
        raise PermissionDenied("Unsupported subscription action.")

    expected, new_status = transitions[action]

    with transaction.atomic():
        subscription = get_object_or_404(
            Subscription.objects.select_for_update(),
            pk=pk,
        )

        if subscription.status != expected:
            messages.error(request, "Invalid subscription status transition.")
            return redirect("subscription_detail", pk=pk)

        if action == "pause" and not subscription.plan.allow_pause:
            raise PermissionDenied("Pausing is not allowed for this plan.")

        if action == "close" and not subscription.plan.allow_early_close:
            raise PermissionDenied("Early closure is not allowed for this plan.")

        subscription.status = new_status
        subscription.save(update_fields=["status"])

    messages.success(request, f"Subscription status: {new_status}.")
    return redirect("subscription_detail", pk=pk)


# =========================================================
# INVOICES
# =========================================================

@login_required
def invoice_list(request):
    invoices = invoice_queryset(request.user)
    status = request.GET.get("status", "")
    search = request.GET.get("q", "").strip()

    valid_statuses = {choice[0] for choice in Invoice.STATUS_CHOICES}
    if status in valid_statuses:
        invoices = invoices.filter(status=status)

    if search:
        invoices = invoices.filter(
            Q(invoice_number__icontains=search)
            | Q(customer__email__icontains=search)
        )

    page_obj = Paginator(
        invoices.order_by("-created_at"), 15
    ).get_page(request.GET.get("page"))

    return render(request, "invoices/invoice_list.html", {
        "page_obj": page_obj,
        "selected_status": status,
        "search": search,
        "statuses": Invoice.STATUS_CHOICES,
    })


@login_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(invoice_queryset(request.user), pk=pk)
    return render(request, "invoices/invoice_detail.html", {
        "invoice": invoice,
    })


@require_POST
@require_roles("admin", "staff")
def invoice_confirm(request, pk):
    with transaction.atomic():
        invoice = get_object_or_404(
            Invoice.objects.select_for_update(), pk=pk
        )

        if invoice.status != "draft":
            messages.error(request, "Only draft invoices can be confirmed.")
            return redirect("invoice_detail", pk=pk)

        if invoice.total_amount <= 0:
            raise ValidationError("Invoice total must be positive.")

        invoice.status = "confirmed"
        invoice.save(update_fields=["status"])

    messages.success(request, "Invoice confirmed.")
    return redirect("invoice_detail", pk=pk)


@require_POST
@require_roles("admin", "staff")
def invoice_cancel(request, pk):
    with transaction.atomic():
        invoice = get_object_or_404(
            Invoice.objects.select_for_update(), pk=pk
        )

        if invoice.status in {"paid", "cancelled"}:
            messages.error(request, "This invoice cannot be cancelled.")
            return redirect("invoice_detail", pk=pk)

        if invoice.payments.filter(status="recorded").exists():
            messages.error(
                request,
                "Reconcile or reverse payments before cancelling.",
            )
            return redirect("invoice_detail", pk=pk)

        invoice.status = "cancelled"
        invoice.save(update_fields=["status"])

    messages.success(request, "Invoice cancelled.")
    return redirect("invoice_detail", pk=pk)


# =========================================================
# RECURRING BILLING
# =========================================================

def next_billing_date(current, interval):
    if interval == "daily":
        return current + timedelta(days=1)

    if interval == "weekly":
        return current + timedelta(days=7)

    if interval == "monthly":
        year = current.year + current.month // 12
        month = current.month % 12 + 1
        day = min(current.day, monthrange(year, month)[1])
        return date(year, month, day)

    if interval == "yearly":
        year = current.year + 1
        day = min(current.day, monthrange(year, current.month)[1])
        return date(year, current.month, day)

    raise ValidationError("Unsupported billing interval.")


@require_POST
@require_roles("admin", "staff")
def generate_invoice(request, subscription_id):
    try:
        with transaction.atomic():
            subscription = get_object_or_404(
                Subscription.objects.select_for_update().prefetch_related(
                    "lines__product__taxes"
                ),
                pk=subscription_id,
            )

            if subscription.status != "active":
                raise ValidationError(
                    "Only active subscriptions can be billed."
                )

            period_start = (
                subscription.next_billing_date or subscription.start_date
            )

            if period_start > timezone.localdate():
                raise ValidationError("This subscription is not due yet.")

            period_next = next_billing_date(
                period_start, subscription.plan.billing_interval
            )
            period_end = period_next - timedelta(days=1)

            if Invoice.objects.filter(
                subscription=subscription,
                billing_period_start=period_start,
                billing_period_end=period_end,
            ).exclude(status="cancelled").exists():
                raise ValidationError(
                    "An invoice already exists for this billing period."
                )

            invoice = Invoice.objects.create(
                invoice_number=generate_number("INV"),
                customer=subscription.customer,
                subscription=subscription,
                issue_date=timezone.localdate(),
                due_date=timezone.localdate() + timedelta(days=14),
                billing_period_start=period_start,
                billing_period_end=period_end,
                subtotal_amount=Decimal("0.00"),
                discount_amount=Decimal("0.00"),
                tax_amount=Decimal("0.00"),
                total_amount=Decimal("0.00"),
                status="draft",
            )

            subtotal = Decimal("0.00")
            total_tax = Decimal("0.00")

            for line in subscription.lines.all():
                line_subtotal = line.unit_price * line.quantity
                tax_rate = line.product.taxes.filter(
                    active=True
                ).aggregate(total=Sum("rate"))["total"] or Decimal("0.00")

                tax_amount = (
                    line_subtotal * tax_rate / Decimal("100")
                ).quantize(Decimal("0.01"))

                InvoiceLine.objects.create(
                    invoice=invoice,
                    product=line.product,
                    description=line.description or line.product.name,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    tax_rate=tax_rate,
                    discount_amount=Decimal("0.00"),
                )

                subtotal += line_subtotal
                total_tax += tax_amount

            invoice.subtotal_amount = subtotal
            invoice.tax_amount = total_tax
            invoice.total_amount = subtotal + total_tax
            invoice.save(update_fields=[
                "subtotal_amount",
                "tax_amount",
                "total_amount",
            ])

            if invoice.total_amount <= 0:
                raise ValidationError("Invoice total must be positive.")

            subscription.next_billing_date = period_next
            subscription.save(update_fields=["next_billing_date"])

        messages.success(request, "Draft invoice generated.")

    except (ValidationError, ValueError) as exc:
        message = (
            "; ".join(exc.messages)
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        messages.error(request, message)
        return redirect("subscription_detail", pk=subscription_id)

    return redirect("invoice_detail", pk=invoice.pk)


# =========================================================
# PAYMENTS
# =========================================================

@require_POST
@require_roles("admin", "staff")
def record_payment(request, invoice_id):
    try:
        amount = positive_decimal(
            request.POST.get("amount"), "Payment amount"
        )
        method = request.POST.get("method", "").strip()

        valid_methods = {choice[0] for choice in Payment.METHOD_CHOICES}
        if method not in valid_methods:
            raise ValidationError("Select a valid payment method.")

        with transaction.atomic():
            invoice = get_object_or_404(
                Invoice.objects.select_for_update(),
                pk=invoice_id,
            )

            if invoice.status in {"draft", "cancelled"}:
                raise ValidationError(
                    "This invoice cannot receive payments."
                )

            paid = invoice.payments.filter(
                status="recorded"
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

            outstanding = invoice.total_amount - paid

            if outstanding <= 0:
                raise ValidationError("Invoice is already fully paid.")

            if amount > outstanding:
                raise ValidationError(
                    f"Payment exceeds the outstanding balance ({outstanding})."
                )

            Payment.objects.create(
                invoice=invoice,
                amount=amount,
                method=method,
                payment_date=timezone.localdate(),
                note=request.POST.get("note", "").strip(),
                status="recorded",
                recorded_by=request.user,
            )

            if paid + amount >= invoice.total_amount:
                invoice.status = "paid"
                invoice.save(update_fields=["status"])

        messages.success(request, "Payment recorded successfully.")

    except (ValidationError, InvalidOperation, ValueError) as exc:
        message = (
            "; ".join(exc.messages)
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        messages.error(request, message)

    return redirect("invoice_detail", pk=invoice_id)


# =========================================================
# FINANCIAL REPORTS
# =========================================================

@require_roles("admin", "staff")
def reports(request):
    start_text = request.GET.get("start_date", "").strip()
    end_text = request.GET.get("end_date", "").strip()

    start_date = parse_date(start_text) if start_text else None
    end_date = parse_date(end_text) if end_text else None

    if start_text and not start_date:
        messages.error(request, "Invalid start date.")

    if end_text and not end_date:
        messages.error(request, "Invalid end date.")

    if start_date and end_date and end_date < start_date:
        messages.error(request, "End date cannot precede start date.")
        end_date = None

    invoices = Invoice.objects.exclude(status="cancelled")
    payments = Payment.objects.filter(status="recorded")

    if start_date:
        invoices = invoices.filter(issue_date__gte=start_date)
        payments = payments.filter(payment_date__gte=start_date)

    if end_date:
        invoices = invoices.filter(issue_date__lte=end_date)
        payments = payments.filter(payment_date__lte=end_date)

    overdue = Invoice.objects.filter(
        due_date__lt=timezone.localdate()
    ).exclude(status__in=["paid", "cancelled"])

    context = {
        "start_date": start_text,
        "end_date": end_text,
        "active_subscriptions": Subscription.objects.filter(
            status="active"
        ).count(),
        "total_invoiced": invoices.aggregate(
            total=Sum("total_amount")
        )["total"] or Decimal("0.00"),
        "revenue_received": payments.aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0.00"),
        "overdue_amount": overdue.aggregate(
            total=Sum("total_amount")
        )["total"] or Decimal("0.00"),
        "overdue_invoices": overdue.select_related(
            "customer"
        ).order_by("due_date"),
        "invoice_status_summary": invoices.values(
            "status"
        ).annotate(count=Count("id")).order_by("status"),
    }

    return render(request, "reports/reports.html", context)
