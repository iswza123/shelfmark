from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.contrib.messages.views import SuccessMessageMixin
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from .forms import RegisterForm


def register(request):
    if request.user.is_authenticated:
        return redirect('post_login')

    form = RegisterForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, f'Welcome to Shelfmark, {user.first_name or user.username}.')
        return redirect('post_login')

    return render(request, 'accounts/register.html', {'form': form})


class SiteLoginView(LoginView):
    template_name = 'accounts/login.html'
    redirect_authenticated_user = True


class SitePasswordChangeView(SuccessMessageMixin, PasswordChangeView):
    template_name = 'accounts/password_change.html'
    success_url = reverse_lazy('post_login')
    success_message = 'Your password has been changed.'
