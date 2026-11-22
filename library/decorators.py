from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect


def staff_required(view):
    """Librarians (is_staff) only. Anonymous users are sent to log in."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())

        if not request.user.is_staff:
            messages.error(request, 'That page is for librarians only.')
            return redirect('home')

        return view(request, *args, **kwargs)

    return wrapper
