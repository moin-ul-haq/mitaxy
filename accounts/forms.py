from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    PasswordResetForm,
    SetPasswordForm,
)
from django.contrib.auth.password_validation import validate_password

User = get_user_model()

_INPUT = {"class": "field__input"}


class StyledPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(
        label="Email",
        max_length=254,
        widget=forms.EmailInput(attrs={**_INPUT, "placeholder": "you@company.com", "autocomplete": "email"}),
    )


class StyledSetPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        placeholders = {"new_password1": "New password", "new_password2": "Repeat new password"}
        for name, field in self.fields.items():
            field.widget.attrs.update({**_INPUT, "placeholder": placeholders.get(name, "")})


class ProfileForm(forms.ModelForm):
    full_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={**_INPUT, "placeholder": "Your name", "autocomplete": "name"}),
    )

    class Meta:
        model = User
        fields = ["full_name"]


class StyledPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        placeholders = {
            "old_password": "Current password",
            "new_password1": "New password",
            "new_password2": "Repeat new password",
        }
        for name, field in self.fields.items():
            field.widget.attrs.update({**_INPUT, "placeholder": placeholders.get(name, "")})


class RegisterForm(forms.ModelForm):
    full_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={**_INPUT, "placeholder": "Jane Doe", "autocomplete": "name"}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={**_INPUT, "placeholder": "you@company.com", "autocomplete": "email"}),
    )
    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={**_INPUT, "placeholder": "At least 8 characters", "autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs={**_INPUT, "placeholder": "Re-enter password", "autocomplete": "new-password"}),
    )

    class Meta:
        model = User
        fields = ["full_name", "email"]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords don't match.")
        if p1:
            validate_password(p1)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.full_name = self.cleaned_data["full_name"]
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class EmailLoginForm(AuthenticationForm):
    """Login form that presents the username field as Email."""

    username = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={**_INPUT, "placeholder": "you@company.com", "autocomplete": "email", "autofocus": True}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].widget = forms.PasswordInput(
            attrs={**_INPUT, "placeholder": "Your password", "autocomplete": "current-password"}
        )
