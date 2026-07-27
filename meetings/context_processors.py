from django.conf import settings


def branding(request):
    """Expose brand strings + feature flags to every template."""
    return {
        "BRAND_NAME": settings.BRAND_NAME,
        "BRAND_TAGLINE": settings.BRAND_TAGLINE,
        "SITE_URL": settings.SITE_URL,
        "GOOGLE_ENABLED": bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET),
    }
