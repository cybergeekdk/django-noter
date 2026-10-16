from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class PagesTests(TestCase):
    def test_home_and_about_render_with_menu(self):
        for name in ("pages:home", "pages:about"):
            response = self.client.get(reverse(name))
            self.assertContains(response, "navbar")
            self.assertContains(response, reverse("notes:list"))

    def test_active_menu_item(self):
        response = self.client.get(reverse("pages:about"))
        self.assertContains(response, 'nav-link active" href="/om/"')

    def test_admin_link_only_for_staff(self):
        self.assertNotContains(self.client.get(reverse("pages:home")), reverse("admin:index"))
        staff = get_user_model().objects.create_user("staff", password="pw", is_staff=True)
        self.client.force_login(staff)
        self.assertContains(self.client.get(reverse("pages:home")), reverse("admin:index"))


class LoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("anna", password="hemmelig123")

    def test_menu_shows_login_when_anonymous(self):
        response = self.client.get(reverse("pages:home"))
        self.assertContains(response, "Log ind")
        self.assertNotContains(response, "Log ud")

    def test_login_redirects_and_menu_shows_user(self):
        response = self.client.post(reverse("pages:login"), {"username": "anna", "password": "hemmelig123"})
        self.assertRedirects(response, reverse("pages:home"))
        page = self.client.get(reverse("pages:home"))
        self.assertContains(page, "anna")
        self.assertContains(page, "Log ud")

    def test_login_respects_next(self):
        response = self.client.post(
            reverse("pages:login") + "?next=/noter/", {"username": "anna", "password": "hemmelig123"}
        )
        self.assertRedirects(response, "/noter/")

    def test_wrong_password_shows_error(self):
        response = self.client.post(reverse("pages:login"), {"username": "anna", "password": "forkert"})
        self.assertContains(response, "Forkert brugernavn eller adgangskode.")

    def test_logout_requires_post_and_logs_out(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("pages:logout")).status_code, 405)
        response = self.client.post(reverse("pages:logout"))
        self.assertRedirects(response, reverse("pages:home"))
        self.assertNotContains(self.client.get(reverse("pages:home")), "Log ud")
