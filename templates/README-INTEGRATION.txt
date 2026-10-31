BRIGHTLOOP DJANGO INTEGRATION NOTES

1. Keep app/models.py and the existing database schema unchanged.
2. Project templates are in the root templates/ directory.
3. Authentication pages use auth_base.html and do not inherit the dashboard sidebar.
4. Customer registration activates the customer account immediately.
5. Staff registration creates an inactive user. An administrator must approve it in Django Admin by activating the user account. The UserProfile role is already set to staff.
6. Administrator access is not available through public registration. Create administrators using `python manage.py createsuperuser`, or have an existing administrator provision an authorized account.
7. Login URL: /login/ ; Registration URL: /register/ ; Django Admin: /admin/.
8. Run `python manage.py check` and `python manage.py migrate` before starting the server.
9. This archive does not include production security hardening. Keep DEBUG enabled only for local development; use HTTPS, secure cookies, a secret from environment variables, and a production web server before deployment.

The application views were aligned to the actual model field names, including Product.is_active, TaxRate.is_active, Subscription.number, Invoice.number, Invoice.subtotal, and the TextChoices classes.
