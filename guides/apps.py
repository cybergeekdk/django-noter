from django.apps import AppConfig


class GuidesConfig(AppConfig):
    name = "guides"
    verbose_name = "Guider"

    def ready(self):
        from . import signals  # noqa: F401
