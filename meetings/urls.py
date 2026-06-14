from django.urls import path

from . import views

app_name = "meetings"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("about/", views.about, name="about"),
    path("contact/", views.contact, name="contact"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("schedule/", views.schedule, name="schedule"),
    path("meetings/<int:pk>/", views.detail, name="detail"),
    path("meetings/<int:pk>/deploy-now/", views.deploy_now, name="deploy_now"),
    path("api/statuses/", views.statuses, name="statuses"),
    path("api/webhooks/recall/", views.recall_webhook, name="recall_webhook"),
]
