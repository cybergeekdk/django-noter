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


from .models import Category  # noqa: E402


class CategoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.work = Category.objects.create(name="Test-arbejde", color="primary")
        cls.home = Category.objects.create(name="Test-hjem", color="success")
        cls.a = Note.objects.create(title="Møde", category=cls.work)
        cls.b = Note.objects.create(title="Rengøring", category=cls.home)
        cls.c = Note.objects.create(title="Uden kategori")

    def test_default_categories_exist_from_migration(self):
        self.assertTrue(Category.objects.filter(name="Privat").exists())

    def test_filter_by_category(self):
        response = self.client.get(reverse("notes:list"), {"kategori": self.work.pk}, **AJAX)
        self.assertContains(response, "Møde")
        self.assertNotContains(response, "Rengøring")
        self.assertNotContains(response, "Uden kategori")

    def test_filter_combines_with_search(self):
        response = self.client.get(reverse("notes:list"), {"kategori": self.work.pk, "q": "Rengøring"}, **AJAX)
        self.assertNotContains(response, "Møde")
        self.assertNotContains(response, "Rengøring")

    def test_invalid_category_param_is_ignored(self):
        for value in ("abc", "-1", "", "1; DROP"):
            response = self.client.get(reverse("notes:list"), {"kategori": value}, **AJAX)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Møde")

    def test_page_shows_badge_and_filter_options(self):
        response = self.client.get(reverse("notes:list"))
        self.assertContains(response, "text-bg-primary")
        self.assertContains(response, "Test-hjem")

    def test_selected_category_is_preselected(self):
        response = self.client.get(reverse("notes:list"), {"kategori": self.home.pk})
        self.assertContains(response, f'value="{self.home.pk}" selected')

    def test_create_note_with_category(self):
        response = self.client.post(
            reverse("notes:list"), {"title": "Ny", "category": self.home.pk}, **AJAX
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Note.objects.get(title="Ny").category, self.home)

    def test_create_with_unknown_category_fails(self):
        response = self.client.post(reverse("notes:list"), {"title": "Ny", "category": 99999}, **AJAX)
        self.assertEqual(response.status_code, 400)
        self.assertIn("category", response.json()["errors"])

    def test_deleting_category_keeps_notes(self):
        self.work.delete()
        self.a.refresh_from_db()
        self.assertIsNone(self.a.category)

    def test_category_name_unique(self):
        from django.db import IntegrityError

        with self.assertRaises(IntegrityError):
            Category.objects.create(name="Test-hjem")
