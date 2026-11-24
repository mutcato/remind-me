from django.urls import path

from .views import HealthView, InstagramWebhookView

urlpatterns = [
    # Meta posts to exactly the URL you register, so both spellings are served
    # and neither one redirects.
    path("webhooks/instagram/", InstagramWebhookView.as_view(), name="instagram-webhook"),
    path("webhooks/instagram", InstagramWebhookView.as_view(), name="instagram-webhook-noslash"),
    path("healthz", HealthView.as_view(), name="healthz"),
]
