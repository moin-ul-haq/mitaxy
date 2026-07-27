"""SEO endpoints: /sitemap.xml and /robots.txt (no sites-framework dependency)."""
from django.conf import settings
from django.http import HttpResponse
from django.urls import reverse
from django.views.decorators.cache import cache_page
from django.views.decorators.http import require_GET

# (url_name, priority, changefreq) — public, indexable marketing pages only.
# (login/register are noindex form pages — deliberately not listed.)
SITEMAP_PAGES = [
    ("meetings:landing", "1.0", "weekly"),
    ("meetings:about", "0.7", "monthly"),
    ("meetings:contact", "0.6", "monthly"),
]


@require_GET
@cache_page(60 * 60 * 12)
def sitemap_xml(request):
    base = settings.SITE_URL
    entries = []
    for name, priority, changefreq in SITEMAP_PAGES:
        entries.append(
            "  <url>\n"
            f"    <loc>{base}{reverse(name)}</loc>\n"
            f"    <changefreq>{changefreq}</changefreq>\n"
            f"    <priority>{priority}</priority>\n"
            "  </url>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>\n"
    )
    return HttpResponse(xml, content_type="application/xml")


@require_GET
@cache_page(60 * 60 * 12)
def robots_txt(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /dashboard/",
        "Disallow: /meetings/",
        "Disallow: /schedule/",
        "Disallow: /accounts/settings/",
        "Disallow: /accounts/onboarding/",
        "Disallow: /accounts/google/",
        "Disallow: /accounts/password-reset/",
        "Disallow: /accounts/reset/",
        "Disallow: /accounts/logout/",
        "Disallow: /s/",
        "Disallow: /api/",
        "",
        f"Sitemap: {settings.SITE_URL}/sitemap.xml",
        "",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")
