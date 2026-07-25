import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView

from . import oauth
from .forms import (
    EmailLoginForm,
    ProfileForm,
    RegisterForm,
    StyledPasswordChangeForm,
    StyledPasswordResetForm,
    StyledSetPasswordForm,
)
from .models import AuthProvider

logger = logging.getLogger("mitaxy")
User = get_user_model()


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
        # Welcome email (best-effort, off the request thread via Celery).
        from meetings.tasks import send_account_email

        try:
            send_account_email.delay(user.pk, "Welcome to Mitaxy", "welcome", {})
        except Exception:
            logger.exception("welcome email enqueue failed")
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
    form_class = StyledPasswordResetForm
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
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


# ---- Google OAuth ----
def google_start(request):
    if not oauth.enabled():
        messages.error(request, "Google sign-in isn't available right now.")
        return redirect("accounts:login")
    return redirect(oauth.build_auth_url(request))


def google_callback(request):
    if not oauth.enabled():
        return redirect("accounts:login")
    error = request.GET.get("error")
    if error:
        messages.error(request, "Google sign-in was cancelled.")
        return redirect("accounts:login")

    code = request.GET.get("code")
    state = request.GET.get("state")
    if not code or not state:
        messages.error(request, "Google sign-in failed. Please try again.")
        return redirect("accounts:login")

    try:
        info = oauth.exchange_code(request, code, state)
    except oauth.GoogleOAuthError as exc:
        logger.warning("google oauth failed: %s", exc)
        messages.error(request, "We couldn't sign you in with Google. Please try again.")
        return redirect("accounts:login")

    user = User.objects.filter(email__iexact=info["email"]).first()
    created = False
    if user is None:
        user = User(
            email=info["email"],
            full_name=info["full_name"],
            auth_provider=AuthProvider.GOOGLE,
        )
        user.set_unusable_password()
        user.save()
        created = True
    else:
        # Existing email account signing in via Google: keep it, enrich the name.
        if not user.full_name and info["full_name"]:
            user.full_name = info["full_name"]
            user.save(update_fields=["full_name"])

    if not user.is_active:
        messages.error(request, "This account is disabled.")
        return redirect("accounts:login")

    login(request, user, backend="accounts.backends.EmailBackend")

    if created:
        from meetings.tasks import send_account_email

        try:
            send_account_email.delay(user.pk, "Welcome to Mitaxy", "welcome", {})
        except Exception:
            logger.exception("welcome email enqueue failed")
        messages.success(request, "Your account is ready. Welcome aboard!")
    return redirect("meetings:dashboard")


# ---- Onboarding ----
@login_required
@require_POST
def onboarding_complete(request):
    request.user.onboarding_completed = True
    request.user.save(update_fields=["onboarding_completed"])
    return redirect(request.POST.get("next") or "meetings:dashboard")


@login_required
@require_POST
def onboarding_restart(request):
    request.user.onboarding_completed = False
    request.user.save(update_fields=["onboarding_completed"])
    return redirect("meetings:dashboard")


# ---- Settings ----
@login_required
def settings_view(request):
    profile_form = ProfileForm(instance=request.user)
    password_form = StyledPasswordChangeForm(user=request.user)

    if request.method == "POST":
        section = request.POST.get("section")
        if section == "profile":
            profile_form = ProfileForm(request.POST, instance=request.user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Profile updated.")
                return redirect("accounts:settings")
        elif section == "password":
            password_form = StyledPasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)  # keep the session alive
                messages.success(request, "Password changed.")
                return redirect("accounts:settings")

    return render(request, "accounts/settings.html", {
        "profile_form": profile_form,
        "password_form": password_form,
        "has_password": request.user.has_usable_password(),
    })
