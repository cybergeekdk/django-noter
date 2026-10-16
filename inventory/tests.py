import csv
import io
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Item

User = get_user_model()
TODAY = date(2026, 6, 15)


def freeze_today():
    return mock.patch("django.utils.timezone.localdate", return_value=TODAY)


class InventoryTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna", password="pw")
        cls.bo = User.objects.create_user("bo", password="pw")
        cls.boss = User.objects.create_superuser("boss", password="pw")

    def make(self, name="Boremaskine", owner=None, **kwargs):
        return Item.objects.create(owner=owner or self.anna, name=name, **kwargs)


class WarrantyStatusTests(InventoryTestCase):
    def status(self, expires):
        with freeze_today():
            return self.make(warranty_expires_on=expires).warranty_status

    def test_statuses(self):
        self.assertEqual(self.status(None), Item.Warranty.NONE)
        self.assertEqual(self.status(date(2026, 6, 14)), Item.Warranty.EXPIRED)  # i går
        self.assertEqual(self.status(TODAY), Item.Warranty.SOON)  # udløber i dag er stadig gyldig
        self.assertEqual(self.status(date(2026, 8, 14)), Item.Warranty.SOON)  # præcis 60 dage
        self.assertEqual(self.status(date(2026, 8, 15)), Item.Warranty.ACTIVE)  # 61 dage
        self.assertEqual(self.status(date(2030, 1, 1)), Item.Warranty.ACTIVE)

    def test_days_left(self):
        with freeze_today():
            self.assertEqual(self.make(warranty_expires_on=date(2026, 6, 20)).warranty_days_left, 5)
            self.assertEqual(self.make(warranty_expires_on=date(2026, 6, 10)).warranty_days_left, -5)
            self.assertIsNone(self.make().warranty_days_left)


class PrivacyTests(InventoryTestCase):
    def setUp(self):
        self.item = self.make("Annas hemmelige ting", location="Safe")

    def test_anonymous_redirected_to_login(self):
        for name, args in [("list", []), ("create", []), ("export", []), ("update", [self.item.pk]), ("delete", [self.item.pk])]:
            response = self.client.get(reverse(f"inventory:{name}", args=args))
            self.assertEqual(response.status_code, 302, name)
            self.assertIn(reverse("accounts:login"), response["Location"])

    def test_other_users_and_superadmin_get_404(self):
        for who in (self.bo, self.boss):
            self.client.force_login(who)
            for name in ("update", "delete"):
                self.assertEqual(self.client.get(reverse(f"inventory:{name}", args=[self.item.pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse("inventory:delete", args=[self.item.pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse("inventory:update", args=[self.item.pk]), {"name": "Hacket", "quantity": 1}).status_code, 404)
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, "Annas hemmelige ting")

    def test_list_and_export_only_contain_own_items(self):
        self.make("Bos ting", owner=self.bo)
        self.client.force_login(self.bo)
        page = self.client.get(reverse("inventory:list"))
        self.assertContains(page, "Bos ting")
        self.assertNotContains(page, "Annas hemmelige ting")
        self.assertNotContains(page, "Safe")  # heller ikke i placerings-filteret
        export = self.client.get(reverse("inventory:export")).content.decode()
        self.assertIn("Bos ting", export)
        self.assertNotIn("Annas", export)

    def test_menu_link_only_when_logged_in(self):
        self.assertNotContains(self.client.get(reverse("pages:home")), reverse("inventory:list"))
        self.client.force_login(self.bo)
        self.assertContains(self.client.get(reverse("pages:home")), reverse("inventory:list"))


class CrudTests(InventoryTestCase):
    def setUp(self):
        self.client.force_login(self.anna)

    def test_create_with_all_fields(self):
        response = self.client.post(reverse("inventory:create"), {
            "name": "Akku-skruetrækker", "location": "Garage – hylde 2", "quantity": 3,
            "purchased_on": "2025-03-01", "warranty_expires_on": "2027-03-01", "notes": "Bosch, kvittering i mappen",
            "owner": self.bo.pk,
        })
        self.assertRedirects(response, reverse("inventory:list"))
        item = Item.objects.get()
        self.assertEqual(item.owner, self.anna)  # owner kan ikke sættes udefra
        self.assertEqual((item.quantity, item.location), (3, "Garage – hylde 2"))
        self.assertEqual(item.purchased_on, date(2025, 3, 1))
        self.assertEqual(item.warranty_expires_on, date(2027, 3, 1))

    def test_only_name_is_required(self):
        self.client.post(reverse("inventory:create"), {"name": "Hammer", "quantity": 1})
        item = Item.objects.get()
        self.assertIsNone(item.purchased_on)
        self.assertIsNone(item.warranty_expires_on)
        self.assertEqual(item.location, "")

    def test_validation_errors(self):
        url = reverse("inventory:create")
        bad = [
            {"name": "", "quantity": 1},
            {"name": "x", "quantity": -1},
            {"name": "x", "quantity": "mange"},
            {"name": "x", "quantity": 1, "purchased_on": "ikke-en-dato"},
            {"name": "x", "quantity": 1, "purchased_on": "2025-05-01", "warranty_expires_on": "2025-01-01"},
        ]
        for data in bad:
            self.assertEqual(self.client.post(url, data).status_code, 200, data)
        self.assertFalse(Item.objects.exists())

    def test_warranty_before_purchase_message(self):
        response = self.client.post(reverse("inventory:create"), {
            "name": "x", "quantity": 1, "purchased_on": "2025-05-01", "warranty_expires_on": "2025-01-01"})
        self.assertContains(response, "Garantien kan ikke udløbe før købsdatoen.")

    def test_quantity_zero_allowed(self):
        self.client.post(reverse("inventory:create"), {"name": "Tom dåse", "quantity": 0})
        self.assertEqual(Item.objects.get().quantity, 0)

    def test_edit_prefills_dates_and_saves(self):
        item = self.make(purchased_on=date(2025, 3, 1), quantity=2)
        url = reverse("inventory:update", args=[item.pk])
        self.assertContains(self.client.get(url), 'value="2025-03-01"')
        self.client.post(url, {"name": "Ny", "quantity": 5, "purchased_on": "2025-03-01"})
        item.refresh_from_db()
        self.assertEqual((item.name, item.quantity), ("Ny", 5))

    def test_delete_requires_post(self):
        item = self.make()
        url = reverse("inventory:delete", args=[item.pk])
        self.assertContains(self.client.get(url), "Boremaskine")
        self.assertTrue(Item.objects.filter(pk=item.pk).exists())
        self.assertRedirects(self.client.post(url), reverse("inventory:list"))
        self.assertFalse(Item.objects.filter(pk=item.pk).exists())

    def test_form_suggests_existing_locations(self):
        self.make(location="Kælder")
        self.make("B", owner=self.bo, location="Bos loft")
        response = self.client.get(reverse("inventory:create"))
        self.assertContains(response, '<option value="Kælder">')
        self.assertNotContains(response, "Bos loft")


class ListFilterTests(InventoryTestCase):
    def setUp(self):
        self.client.force_login(self.anna)
        self.make("Expired", location="Garage", quantity=1, warranty_expires_on=date(2026, 1, 1), purchased_on=date(2024, 1, 1))
        self.make("Soon", location="Garage", quantity=4, warranty_expires_on=date(2026, 7, 1), purchased_on=date(2025, 5, 1))
        self.make("Active", location="Kælder", quantity=2, warranty_expires_on=date(2029, 1, 1), purchased_on=date(2026, 2, 1))
        self.make("NoWarranty", location="", quantity=10)

    def names(self, **params):
        with freeze_today():
            response = self.client.get(reverse("inventory:list"), params)
        return [i.name for i in response.context["items"]]

    def test_default_sort_by_name(self):
        self.assertEqual(self.names(), ["Active", "Expired", "NoWarranty", "Soon"])

    def test_warranty_filters(self):
        self.assertEqual(self.names(garanti="udloebet"), ["Expired"])
        self.assertEqual(self.names(garanti="snart"), ["Soon"])
        self.assertEqual(self.names(garanti="aktiv"), ["Active"])
        self.assertEqual(self.names(garanti="ingen"), ["NoWarranty"])

    def test_location_filter(self):
        self.assertEqual(self.names(sted="Garage"), ["Expired", "Soon"])
        self.assertEqual(self.names(sted="__ingen__"), ["NoWarranty"])

    def test_search_in_name_location_and_notes(self):
        self.make("Støvsuger", notes="serienr 12345")
        self.assertEqual(self.names(q="kælder"), ["Active"])
        self.assertEqual(self.names(q="12345"), ["Støvsuger"])
        self.assertEqual(self.names(q="soon"), ["Soon"])

    def test_filters_combine(self):
        self.assertEqual(self.names(sted="Garage", garanti="snart"), ["Soon"])
        self.assertEqual(self.names(sted="Kælder", garanti="udloebet"), [])

    def test_sorting_with_nulls_last(self):
        self.assertEqual(self.names(sorter="garanti"), ["Expired", "Soon", "Active", "NoWarranty"])
        self.assertEqual(self.names(sorter="koebt"), ["Active", "Soon", "Expired", "NoWarranty"])
        self.assertEqual(self.names(sorter="antal"), ["NoWarranty", "Soon", "Active", "Expired"])

    def test_unknown_sort_and_filter_values_are_harmless(self):
        for params in ({"sorter": "password; DROP"}, {"garanti": "x"}, {"sted": "' OR 1=1"}):
            with freeze_today():
                self.assertEqual(self.client.get(reverse("inventory:list"), params).status_code, 200)

    def test_summary_numbers_ignore_filters(self):
        with freeze_today():
            response = self.client.get(reverse("inventory:list"), {"sted": "Kælder"})
        ctx = response.context
        self.assertEqual((ctx["total_items"], ctx["total_quantity"]), (4, 17))
        self.assertEqual((ctx["soon_count"], ctx["expired_count"]), (1, 1))

    def test_badges_render(self):
        with freeze_today():
            page = self.client.get(reverse("inventory:list")).content.decode()
        self.assertIn("Udløbet", page)
        self.assertIn("Udløber om 16 dage", page)
        self.assertIn("Garanti OK", page)


class ExportTests(InventoryTestCase):
    def rows(self, **params):
        with freeze_today():
            response = self.client.get(reverse("inventory:export"), params)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("attachment", response["Content-Disposition"])
        text = response.content.decode("utf-8")
        self.assertTrue(text.startswith("﻿"))
        return list(csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=";"))

    def test_export_contents_and_filters(self):
        self.client.force_login(self.anna)
        self.make("A", location="Garage", quantity=2, purchased_on=date(2025, 1, 2), warranty_expires_on=date(2026, 1, 1))
        self.make("B", location="Kælder")
        rows = self.rows()
        self.assertEqual(rows[0][:3], ["Navn", "Placering", "Antal"])
        self.assertEqual(rows[1], ["A", "Garage", "2", "2025-01-02", "2026-01-01", "Udløbet", ""])
        self.assertEqual(len(rows), 3)
        self.assertEqual([r[0] for r in self.rows(sted="Kælder")[1:]], ["B"])

    def test_formula_injection_is_neutralised(self):
        self.client.force_login(self.anna)
        self.make("=HYPERLINK(\"http://ond.dk\")", location="+1+1", notes="@SUM(A1)")
        row = self.rows()[1]
        self.assertTrue(row[0].startswith("'="))
        self.assertTrue(row[1].startswith("'+"))
        self.assertTrue(row[6].startswith("'@"))
