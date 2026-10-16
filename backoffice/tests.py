from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from notes.models import Note

from .sites import BackofficeAdminSite


class BackofficeAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = get_user_model().objects.create_superuser("admin", password="pw")

    def test_default_site_is_custom(self):
        self.assertIsInstance(admin.site, BackofficeAdminSite)
        self.assertTrue(admin.site.is_registered(Note))

    def test_index_requires_login(self):
        response = self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code, 302)

    def test_dashboard_shows_stats(self):
        Note.objects.create(title="Første")
        Note.objects.create(title="Anden")
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "noter i alt")
        self.assertEqual(response.context["note_count"], 2)
        self.assertContains(response, "Anden")

    def test_note_changelist_search(self):
        Note.objects.create(title="Indkøb", body="mælk")
        Note.objects.create(title="Regnskab")
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("admin:notes_note_changelist"), {"q": "mælk"})
        self.assertContains(response, "Indkøb")
        self.assertNotContains(response, "Regnskab")
