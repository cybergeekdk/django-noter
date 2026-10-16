from django.contrib.admin.apps import AdminConfig


class BackofficeAdminConfig(AdminConfig):
    """Erstatter django.contrib.admin, så admin.site er vores egen AdminSite."""

    default_site = "backoffice.sites.BackofficeAdminSite"
