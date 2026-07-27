from django.urls import path

from . import seo, views

app_name = "meetings"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("sitemap.xml", seo.sitemap_xml, name="sitemap"),
    path("robots.txt", seo.robots_txt, name="robots"),
    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="contact"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("schedule/", views.schedule, name="schedule"),
    path("meetings/<int:pk>/", views.detail, name="detail"),
    path("meetings/<int:pk>/deploy-now/", views.deploy_now, name="deploy_now"),
    path("meetings/<int:pk>/cancel/", views.cancel, name="cancel"),
    path("meetings/<int:pk>/share/", views.share_update, name="share_update"),
    path("s/<str:token>/", views.shared, name="shared"),
    path("api/statuses/", views.statuses, name="statuses"),
    path("api/webhooks/recall/", views.recall_webhook, name="recall_webhook"),
]
