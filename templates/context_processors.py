from .views import get_role

def user_role(request):
    """Expose the current user's application role to all templates."""
    if request.user.is_authenticated:
        return {"role": get_role(request.user)}
    return {"role": None}
