import logging

from django.apps import AppConfig
from django.conf import settings

log = logging.getLogger(__name__)


class BotConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'bot'
