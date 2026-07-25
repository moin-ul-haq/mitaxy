from django.contrib.auth.models import AbstractUser
from django.db import models

from .managers import UserManager


class AuthProvider(models.TextChoices):
    EMAIL = "email", "Email & password"
    GOOGLE = "google", "Google"


class User(AbstractUser):
    """Email-first user. No username field — email is the identifier."""

    username = None
    email = models.EmailField("email address", unique=True)
    full_name = models.CharField(max_length=150, blank=True)
    auth_provider = models.CharField(
        max_length=16, choices=AuthProvider.choices, default=AuthProvider.EMAIL
    )
    # Set once the user finishes (or skips) the first-run product tour.
    onboarding_completed = models.BooleanField(default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email

    @property
    def display_name(self):
        return self.full_name.strip() or self.email.split("@")[0]

    @property
    def first_name_or_email(self):
        if self.full_name.strip():
            return self.full_name.strip().split()[0]
        return self.email.split("@")[0]
