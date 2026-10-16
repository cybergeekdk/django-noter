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
