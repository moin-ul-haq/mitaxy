from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.RegisterView.as_view(), name="register"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("google/start/", views.google_start, name="google_start"),
    path("google/callback/", views.google_callback, name="google_callback"),
    path("settings/", views.settings_view, name="settings"),
    path("onboarding/complete/", views.onboarding_complete, name="onboarding_complete"),
    path("onboarding/restart/", views.onboarding_restart, name="onboarding_restart"),
    path("password-reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/done/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/done/", views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
]
