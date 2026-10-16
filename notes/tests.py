from django.test import TestCase
from django.urls import reverse

from .models import Note


class NoteViewTests(TestCase):
    def test_create_note(self):
        response = self.client.post(reverse("notes:list"), {"title": "Hej", "body": "Verden"})
        self.assertRedirects(response, reverse("notes:list"))
        self.assertEqual(Note.objects.get().title, "Hej")

    def test_list_shows_notes(self):
        Note.objects.create(title="Indkøb")
        response = self.client.get(reverse("notes:list"))
        self.assertContains(response, "Indkøb")

    def test_delete_note(self):
        note = Note.objects.create(title="Væk")
        self.client.post(reverse("notes:delete", args=[note.pk]))
        self.assertFalse(Note.objects.exists())


AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


class NoteAjaxTests(TestCase):
    def test_search_returns_partial(self):
        Note.objects.create(title="Indkøb", body="mælk")
        Note.objects.create(title="Arbejde")
        response = self.client.get(reverse("notes:list"), {"q": "mælk"}, **AJAX)
        self.assertContains(response, "Indkøb")
        self.assertNotContains(response, "Arbejde")
        self.assertNotContains(response, "<html")

    def test_create_returns_json_html(self):
        response = self.client.post(reverse("notes:list"), {"title": "Ny"}, **AJAX)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Ny", response.json()["html"])

    def test_create_invalid_returns_errors(self):
        response = self.client.post(reverse("notes:list"), {"title": ""}, **AJAX)
        self.assertEqual(response.status_code, 400)
        self.assertIn("title", response.json()["errors"])

    def test_delete_returns_204(self):
        note = Note.objects.create(title="Væk")
        response = self.client.post(reverse("notes:delete", args=[note.pk]), **AJAX)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Note.objects.exists())
