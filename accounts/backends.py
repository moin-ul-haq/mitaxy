from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

UserModel = get_user_model()


class EmailBackend(ModelBackend):
    """Authenticate by email (case-insensitive)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        email = username or kwargs.get("email")
        if email is None or password is None:
            return None
        try:
            user = UserModel.objects.get(email__iexact=email.strip())
        except UserModel.DoesNotExist:
            # Run the default hasher once to reduce timing differences.
            UserModel().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
