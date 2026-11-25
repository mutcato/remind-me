from django.urls import path

from .views import DataDeletionView, HealthView, InstagramWebhookView, PrivacyView

urlpatterns = [
    # Meta posts to exactly the URL you register, so both spellings are served
    # and neither one redirects.
    path("webhooks/instagram/", InstagramWebhookView.as_view(), name="instagram-webhook"),
    path("webhooks/instagram", InstagramWebhookView.as_view(), name="instagram-webhook-noslash"),
    path("healthz", HealthView.as_view(), name="healthz"),
    # Meta requires both URLs before the app can be published.
    path("privacy/", PrivacyView.as_view(), name="privacy"),
    path("data-deletion/", DataDeletionView.as_view(), name="data-deletion"),
]
