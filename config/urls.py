from django.contrib import admin
from django.urls import include, path

# Branded admin chrome (templates/admin/* adds the stats dashboard + styling).
admin.site.site_header = "Mitaxy Administration"
admin.site.site_title = "Mitaxy Admin"
admin.site.index_title = "Overview"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("meetings.urls")),
    path("accounts/", include("accounts.urls")),
]
