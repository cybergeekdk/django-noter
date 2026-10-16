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
