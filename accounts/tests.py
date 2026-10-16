from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


def make_user(name, *, superuser=False):
    return User.objects.create_user(name, password="hemmelig123", is_superuser=superuser, is_staff=superuser)


class LoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user("anna")

    def test_menu_shows_login_when_anonymous(self):
        response = self.client.get(reverse("pages:home"))
        self.assertContains(response, "Log ind")
        self.assertNotContains(response, "Log ud")

    def test_login_redirects_and_menu_shows_user(self):
        response = self.client.post(reverse("accounts:login"), {"username": "anna", "password": "hemmelig123"})
        self.assertRedirects(response, reverse("pages:home"))
        page = self.client.get(reverse("pages:home"))
        self.assertContains(page, "anna")
        self.assertContains(page, "Log ud")

    def test_login_respects_next(self):
        response = self.client.post(
            reverse("accounts:login") + "?next=/noter/", {"username": "anna", "password": "hemmelig123"}
        )
        self.assertRedirects(response, "/noter/")

    def test_wrong_password_shows_error(self):
        response = self.client.post(reverse("accounts:login"), {"username": "anna", "password": "forkert"})
        self.assertContains(response, "Forkert brugernavn eller adgangskode.")

    def test_logout_requires_post_and_logs_out(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("pages:home"))
        self.assertNotContains(self.client.get(reverse("pages:home")), "Log ud")


class SignUpTests(TestCase):
    data = {"username": "bo", "email": "bo@example.dk", "password1": "S3kret-kode-42", "password2": "S3kret-kode-42"}

    def test_signup_creates_regular_user_and_logs_in(self):
        response = self.client.post(reverse("accounts:signup"), self.data)
        self.assertRedirects(response, reverse("pages:home"))
        user = User.objects.get(username="bo")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertContains(self.client.get(reverse("pages:home")), "Log ud")

    def test_signup_ignores_privilege_fields(self):
        self.client.post(reverse("accounts:signup"), {**self.data, "is_superuser": "on", "is_staff": "on"})
        user = User.objects.get(username="bo")
        self.assertFalse(user.is_superuser or user.is_staff)

    def test_password_mismatch(self):
        response = self.client.post(reverse("accounts:signup"), {**self.data, "password2": "andet"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="bo").exists())

    def test_duplicate_username(self):
        make_user("bo")
        response = self.client.post(reverse("accounts:signup"), self.data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username="bo").count(), 1)

    def test_signup_redirects_when_logged_in(self):
        self.client.force_login(make_user("anna"))
        self.assertRedirects(self.client.get(reverse("accounts:signup")), reverse("pages:home"))

    def test_menu_shows_signup_when_anonymous(self):
        self.assertContains(self.client.get(reverse("pages:home")), reverse("accounts:signup"))


class UserManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.boss = make_user("boss", superuser=True)
        cls.anna = make_user("anna")

    def test_list_requires_login(self):
        response = self.client.get(reverse("accounts:user_list"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={reverse('accounts:user_list')}")

    def test_regular_user_gets_403_everywhere(self):
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("accounts:user_delete", args=[self.boss.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("accounts:user_delete", args=[self.boss.pk])).status_code, 403)
        response = self.client.post(reverse("accounts:user_role", args=[self.anna.pk]), {"role": "superadmin"})
        self.assertEqual(response.status_code, 403)
        self.anna.refresh_from_db()
        self.assertFalse(self.anna.is_superuser)
        self.assertTrue(User.objects.filter(pk=self.boss.pk).exists())

    def test_menu_item_only_for_superadmin(self):
        self.client.force_login(self.anna)
        self.assertNotContains(self.client.get(reverse("pages:home")), reverse("accounts:user_list"))
        self.client.force_login(self.boss)
        self.assertContains(self.client.get(reverse("pages:home")), reverse("accounts:user_list"))

    def test_superadmin_sees_user_list(self):
        self.client.force_login(self.boss)
        response = self.client.get(reverse("accounts:user_list"))
        self.assertContains(response, "anna")
        self.assertContains(response, "Super admin")

    def test_superadmin_can_delete_user(self):
        self.client.force_login(self.boss)
        url = reverse("accounts:user_delete", args=[self.anna.pk])
        self.assertContains(self.client.get(url), "anna")
        self.assertRedirects(self.client.post(url), reverse("accounts:user_list"))
        self.assertFalse(User.objects.filter(pk=self.anna.pk).exists())

    def test_get_does_not_delete(self):
        self.client.force_login(self.boss)
        self.client.get(reverse("accounts:user_delete", args=[self.anna.pk]))
        self.assertTrue(User.objects.filter(pk=self.anna.pk).exists())

    def test_cannot_delete_self(self):
        other = make_user("chef", superuser=True)
        self.client.force_login(self.boss)
        self.client.post(reverse("accounts:user_delete", args=[self.boss.pk]))
        self.assertTrue(User.objects.filter(pk=self.boss.pk).exists())
        self.assertTrue(User.objects.filter(pk=other.pk).exists())

    def test_superadmin_can_delete_another_superadmin(self):
        other = make_user("chef", superuser=True)
        self.client.force_login(self.boss)
        self.client.post(reverse("accounts:user_delete", args=[other.pk]))
        self.assertFalse(User.objects.filter(pk=other.pk).exists())

    def test_promote_and_demote(self):
        self.client.force_login(self.boss)
        url = reverse("accounts:user_role", args=[self.anna.pk])
        self.client.post(url, {"role": "superadmin"})
        self.anna.refresh_from_db()
        self.assertTrue(self.anna.is_superuser and self.anna.is_staff)
        self.client.post(url, {"role": "user"})
        self.anna.refresh_from_db()
        self.assertFalse(self.anna.is_superuser or self.anna.is_staff)

    def test_cannot_demote_last_superadmin(self):
        self.client.force_login(self.boss)
        self.client.post(reverse("accounts:user_role", args=[self.boss.pk]), {"role": "user"})
        self.boss.refresh_from_db()
        self.assertTrue(self.boss.is_superuser)

    def test_unknown_role_rejected_and_get_not_allowed(self):
        self.client.force_login(self.boss)
        url = reverse("accounts:user_role", args=[self.anna.pk])
        self.client.post(url, {"role": "gud"})
        self.anna.refresh_from_db()
        self.assertFalse(self.anna.is_superuser)
        self.assertEqual(self.client.get(url).status_code, 405)
