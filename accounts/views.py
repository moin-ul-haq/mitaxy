import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView

from meetings.services.mailer import send_html_email

from .forms import EmailLoginForm, RegisterForm

logger = logging.getLogger("mitaxy")


class RegisterView(CreateView):
    template_name = "accounts/register.html"
    form_class = RegisterForm
    success_url = reverse_lazy("meetings:dashboard")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("meetings:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save()
        # Welcome email (best-effort)
        send_html_email(
            to=user.email,
            subject="Welcome to Mitaxy",
            template="welcome",
            context={"user": user},
        )
        # Log the user straight in (fires the login-alert signal too).
        login(self.request, user, backend="accounts.backends.EmailBackend")
        messages.success(self.request, "Your account is ready. Welcome aboard!")
        return redirect(self.success_url)


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = EmailLoginForm
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    pass


# ---- Password reset (uses Django's flow + our HTML email template) ----
class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "emails/password_reset_email.html"
    html_email_template_name = "emails/password_reset_email.html"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")
    extra_email_context = {
        "brand_name": settings.BRAND_NAME,
        "brand_tagline": settings.BRAND_TAGLINE,
        "site_url": settings.SITE_URL,
        "subject": "Reset your Mitaxy password",
    }


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"
