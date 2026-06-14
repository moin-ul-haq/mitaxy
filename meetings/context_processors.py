from django.conf import settings


def branding(request):
    """Expose brand strings to every template."""
    return {
        "BRAND_NAME": settings.BRAND_NAME,
        "BRAND_TAGLINE": settings.BRAND_TAGLINE,
    }
