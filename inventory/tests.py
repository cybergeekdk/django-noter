import csv
import io
import shutil
import tempfile
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import Attachment, Item, Location, ReminderPreference
from .reminders import send_due_reminders

User = get_user_model()
TODAY = date(2026, 6, 15)
TMP = tempfile.mkdtemp(prefix="inventory-test-")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def freeze_today():
    return mock.patch("django.utils.timezone.localdate", return_value=TODAY)


def png(name="kvittering.png"):
    buffer = BytesIO()
    Image.new("RGB", (30, 20), "tomato").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def pdf(name="kvittering.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF", content_type="application/pdf")


# Skjulte formsætfelter, som en rigtig browser altid sender med, når man redigerer en ting.
NO_ATTACHMENT_FORMS = {
    "files-TOTAL_FORMS": "0", "files-INITIAL_FORMS": "0", "files-MIN_NUM_FORMS": "0", "files-MAX_NUM_FORMS": "1000",
}


@override_settings(PRIVATE_MEDIA_ROOT=TMP)
class InventoryTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna", password="pw", email="anna@example.dk")
        cls.bo = User.objects.create_user("bo", password="pw", email="bo@example.dk")
        cls.boss = User.objects.create_superuser("boss", password="pw")

    def make(self, name="Boremaskine", owner=None, location=None, **kwargs):
        owner = owner or self.anna
        if isinstance(location, str):
            location, _ = Location.objects.get_or_create(owner=owner, name=location)
        return Item.objects.create(owner=owner, name=name, location=location, **kwargs)

    def disk_files(self):
        return [p for p in Path(TMP).rglob("*") if p.is_file()]


class WarrantyStatusTests(InventoryTestCase):
    def status(self, expires):
        with freeze_today():
            return self.make(warranty_expires_on=expires).warranty_status

    def test_statuses(self):
        self.assertEqual(self.status(None), Item.Warranty.NONE)
        self.assertEqual(self.status(date(2026, 6, 14)), Item.Warranty.EXPIRED)
        self.assertEqual(self.status(TODAY), Item.Warranty.SOON)
        self.assertEqual(self.status(date(2026, 8, 14)), Item.Warranty.SOON)
        self.assertEqual(self.status(date(2026, 8, 15)), Item.Warranty.ACTIVE)

    def test_days_left(self):
        with freeze_today():
            self.assertEqual(self.make(warranty_expires_on=date(2026, 6, 20)).warranty_days_left, 5)
            self.assertEqual(self.make(warranty_expires_on=date(2026, 6, 10)).warranty_days_left, -5)
            self.assertIsNone(self.make().warranty_days_left)


class PrivacyTests(InventoryTestCase):
    def setUp(self):
        self.item = self.make("Annas hemmelige ting", location="Safe")
        self.att = Attachment.objects.create(item=self.item, file=png(), label="Hemmelig kvittering")
        self.loc = self.item.location

    def urls(self):
        i, a, l = self.item.pk, self.att.pk, self.loc.pk
        return {
            "detail": reverse("inventory:detail", args=[i]),
            "update": reverse("inventory:update", args=[i]),
            "delete": reverse("inventory:delete", args=[i]),
            "attachment": reverse("inventory:attachment", args=[a]),
            "location_update": reverse("inventory:location_update", args=[l]),
            "location_delete": reverse("inventory:location_delete", args=[l]),
        }

    def test_anonymous_redirected_to_login(self):
        urls = {**self.urls(), "list": reverse("inventory:list"), "create": reverse("inventory:create"),
                "export": reverse("inventory:export"), "locations": reverse("inventory:locations"),
                "reminders": reverse("inventory:reminders")}
        for name, url in urls.items():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, name)
            self.assertIn(reverse("accounts:login"), response["Location"], name)

    def test_other_users_and_superadmin_get_404(self):
        for who in (self.bo, self.boss):
            self.client.force_login(who)
            for name, url in self.urls().items():
                self.assertEqual(self.client.get(url).status_code, 404, f"{who} {name}")
            for name in ("delete", "location_delete", "location_update", "update"):
                self.assertEqual(self.client.post(self.urls()[name], {"name": "Hacket", "quantity": 1}).status_code, 404, name)
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, "Annas hemmelige ting")
        self.assertTrue(Location.objects.filter(pk=self.loc.pk, name="Safe").exists())

    def test_lists_and_export_only_contain_own_data(self):
        self.make("Bos ting", owner=self.bo, location="Bos loft")
        self.client.force_login(self.bo)
        page = self.client.get(reverse("inventory:list"))
        self.assertContains(page, "Bos ting")
        self.assertNotContains(page, "Annas hemmelige ting")
        self.assertNotContains(page, "Safe")
        self.assertNotContains(self.client.get(reverse("inventory:locations")), "Safe")
        export = self.client.get(reverse("inventory:export")).content.decode()
        self.assertIn("Bos ting", export)
        self.assertNotIn("Annas", export)

    def test_cannot_pick_someone_elses_location(self):
        self.client.force_login(self.bo)
        response = self.client.post(reverse("inventory:create"), {"name": "Snyd", "quantity": 1, "location": self.loc.pk})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Item.objects.filter(name="Snyd").exists())

    def test_cannot_touch_someone_elses_attachment_through_formset(self):
        mine = self.make("Min", owner=self.bo)
        mine_att = Attachment.objects.create(item=mine, file=png(), label="min")
        self.client.force_login(self.bo)
        data = {
            "name": "Min", "quantity": 1,
            "files-TOTAL_FORMS": "1", "files-INITIAL_FORMS": "1", "files-MIN_NUM_FORMS": "0", "files-MAX_NUM_FORMS": "1000",
            "files-0-id": self.att.pk, "files-0-item": mine.pk, "files-0-label": "overtaget", "files-0-DELETE": "on",
        }
        self.client.post(reverse("inventory:update", args=[mine.pk]), data)
        self.assertTrue(Attachment.objects.filter(pk=self.att.pk, label="Hemmelig kvittering").exists())
        self.assertTrue(Attachment.objects.filter(pk=mine_att.pk).exists())

    def test_attachment_has_no_public_url(self):
        stored = self.att.file.name
        with self.assertRaises(ValueError):
            self.att.file.url
        self.assertEqual(self.client.get(f"/media/{stored}").status_code, 404)
        self.assertEqual(self.client.get(f"/static/{stored}").status_code, 404)

    def test_menu_link_only_when_logged_in(self):
        self.assertNotContains(self.client.get(reverse("pages:home")), reverse("inventory:list"))
        self.client.force_login(self.bo)
        self.assertContains(self.client.get(reverse("pages:home")), reverse("inventory:list"))


class CrudTests(InventoryTestCase):
    def setUp(self):
        self.client.force_login(self.anna)

    def test_create_with_all_fields(self):
        garage = Location.objects.create(owner=self.anna, name="Garage")
        response = self.client.post(reverse("inventory:create"), {
            "name": "Akku-skruetrækker", "location": garage.pk, "quantity": 3,
            "purchased_on": "2025-03-01", "warranty_expires_on": "2027-03-01", "notes": "Bosch",
            "owner": self.bo.pk,
        })
        self.assertRedirects(response, reverse("inventory:list"))
        item = Item.objects.get()
        self.assertEqual(item.owner, self.anna)
        self.assertEqual((item.quantity, item.location), (3, garage))
        self.assertEqual(item.warranty_expires_on, date(2027, 3, 1))

    def test_only_name_is_required(self):
        self.client.post(reverse("inventory:create"), {"name": "Hammer", "quantity": 1})
        item = Item.objects.get()
        self.assertEqual((item.location, item.purchased_on, item.warranty_expires_on), (None, None, None))

    def test_new_location_is_created_and_reused_case_insensitively(self):
        self.client.post(reverse("inventory:create"), {"name": "A", "quantity": 1, "new_location": "  Kælder  "})
        self.client.post(reverse("inventory:create"), {"name": "B", "quantity": 1, "new_location": "kælder"})
        self.assertEqual(Location.objects.filter(owner=self.anna).count(), 1)
        self.assertEqual(Location.objects.get().name, "Kælder")
        self.assertEqual(Location.objects.get().items.count(), 2)

    def test_new_location_not_created_when_form_invalid(self):
        self.client.post(reverse("inventory:create"), {"name": "", "quantity": 1, "new_location": "Spøgelse"})
        self.assertFalse(Location.objects.exists())

    def test_new_location_beats_dropdown(self):
        old = Location.objects.create(owner=self.anna, name="Gammel")
        self.client.post(reverse("inventory:create"), {"name": "A", "quantity": 1, "location": old.pk, "new_location": "Ny"})
        self.assertEqual(Item.objects.get().location.name, "Ny")

    def test_validation_errors(self):
        url = reverse("inventory:create")
        bad = [
            {"name": "", "quantity": 1},
            {"name": "x", "quantity": -1},
            {"name": "x", "quantity": "mange"},
            {"name": "x", "quantity": 1, "purchased_on": "ikke-en-dato"},
            {"name": "x", "quantity": 1, "purchased_on": "2025-05-01", "warranty_expires_on": "2025-01-01"},
            {"name": "x", "quantity": 1, "location": 99999},
        ]
        for data in bad:
            self.assertEqual(self.client.post(url, data).status_code, 400, data)
        self.assertFalse(Item.objects.exists())

    def test_warranty_before_purchase_message(self):
        response = self.client.post(reverse("inventory:create"), {
            "name": "x", "quantity": 1, "purchased_on": "2025-05-01", "warranty_expires_on": "2025-01-01"})
        self.assertContains(response, "Garantien kan ikke udløbe før købsdatoen.", status_code=400)

    def test_quantity_zero_allowed(self):
        self.client.post(reverse("inventory:create"), {"name": "Tom dåse", "quantity": 0})
        self.assertEqual(Item.objects.get().quantity, 0)

    def test_edit_without_formset_data_is_rejected_not_crashing(self):
        item = self.make()
        response = self.client.post(reverse("inventory:update", args=[item.pk]), {"name": "Ny", "quantity": 5})
        self.assertEqual(response.status_code, 400)

    def test_edit_prefills_and_saves(self):
        item = self.make(purchased_on=date(2025, 3, 1), quantity=2)
        url = reverse("inventory:update", args=[item.pk])
        self.assertContains(self.client.get(url), 'value="2025-03-01"')
        self.client.post(url, {"name": "Ny", "quantity": 5, "purchased_on": "2025-03-01", **NO_ATTACHMENT_FORMS})
        item.refresh_from_db()
        self.assertEqual((item.name, item.quantity), ("Ny", 5))

    def test_delete_requires_post(self):
        item = self.make()
        url = reverse("inventory:delete", args=[item.pk])
        self.assertContains(self.client.get(url), "Boremaskine")
        self.assertTrue(Item.objects.filter(pk=item.pk).exists())
        self.assertRedirects(self.client.post(url), reverse("inventory:list"))
        self.assertFalse(Item.objects.filter(pk=item.pk).exists())

    def test_detail_page(self):
        item = self.make(location="Garage", notes="serienr 42", quantity=2)
        response = self.client.get(reverse("inventory:detail", args=[item.pk]))
        for text in ("Boremaskine", "Garage", "serienr 42"):
            self.assertContains(response, text)


class LocationTests(InventoryTestCase):
    def setUp(self):
        self.client.force_login(self.anna)

    def test_create_rename_and_listing_with_counts(self):
        self.client.post(reverse("inventory:location_create"), {"name": "  Garage   hylde 2 "})
        location = Location.objects.get()
        self.assertEqual(location.name, "Garage hylde 2")  # mellemrum ryddes op
        self.make("A", location=location)
        self.make("B", location=location)
        self.make("C")  # uden placering
        page = self.client.get(reverse("inventory:locations"))
        self.assertContains(page, "Garage hylde 2")
        self.assertEqual(page.context["locations"][0].item_count, 2)
        self.assertEqual(page.context["unplaced_count"], 1)

    def test_rename_propagates_to_all_items(self):
        location = Location.objects.create(owner=self.anna, name="Loft")
        item = self.make(location=location)
        self.client.post(reverse("inventory:location_update", args=[location.pk]), {"name": "Pulterrum"})
        self.assertContains(self.client.get(reverse("inventory:detail", args=[item.pk])), "Pulterrum")
        self.assertNotContains(self.client.get(reverse("inventory:list")), "Loft")

    def test_duplicate_names_rejected_case_insensitively_but_per_user(self):
        Location.objects.create(owner=self.anna, name="Garage")
        response = self.client.post(reverse("inventory:location_create"), {"name": "GARAGE"})
        self.assertContains(response, "Du har allerede en placering med det navn.")
        self.assertEqual(Location.objects.filter(owner=self.anna).count(), 1)
        self.client.force_login(self.bo)  # en anden bruger må gerne have samme navn
        self.client.post(reverse("inventory:location_create"), {"name": "Garage"})
        self.assertEqual(Location.objects.filter(owner=self.bo).count(), 1)

    def test_rename_to_own_name_is_allowed(self):
        location = Location.objects.create(owner=self.anna, name="Garage")
        response = self.client.post(reverse("inventory:location_update", args=[location.pk]), {"name": "Garage"})
        self.assertEqual(response.status_code, 302)

    def test_delete_keeps_items(self):
        location = Location.objects.create(owner=self.anna, name="Loft")
        item = self.make(location=location)
        url = reverse("inventory:location_delete", args=[location.pk])
        self.assertContains(self.client.get(url), "slettes <strong>ikke</strong>")
        self.client.post(url)
        item.refresh_from_db()
        self.assertIsNone(item.location)
        self.assertFalse(Location.objects.exists())

    def test_form_only_offers_own_locations(self):
        Location.objects.create(owner=self.anna, name="Kælder")
        Location.objects.create(owner=self.bo, name="Bos loft")
        page = self.client.get(reverse("inventory:create"))
        self.assertContains(page, "Kælder")
        self.assertNotContains(page, "Bos loft")


class ListFilterTests(InventoryTestCase):
    def setUp(self):
        self.client.force_login(self.anna)
        self.make("Expired", location="Garage", quantity=1, warranty_expires_on=date(2026, 1, 1), purchased_on=date(2024, 1, 1))
        self.make("Soon", location="Garage", quantity=4, warranty_expires_on=date(2026, 7, 1), purchased_on=date(2025, 5, 1))
        self.make("Active", location="Kælder", quantity=2, warranty_expires_on=date(2029, 1, 1), purchased_on=date(2026, 2, 1))
        self.make("NoWarranty", quantity=10)
        self.garage = Location.objects.get(name="Garage")
        self.cellar = Location.objects.get(name="Kælder")

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
        self.assertEqual(self.names(sted=self.garage.pk), ["Expired", "Soon"])
        self.assertEqual(self.names(sted="__ingen__"), ["NoWarranty"])

    def test_search_in_name_location_and_notes(self):
        self.make("Støvsuger", notes="serienr 12345")
        self.assertEqual(self.names(q="kælder"), ["Active"])
        self.assertEqual(self.names(q="12345"), ["Støvsuger"])
        self.assertEqual(self.names(q="soon"), ["Soon"])

    def test_filters_combine(self):
        self.assertEqual(self.names(sted=self.garage.pk, garanti="snart"), ["Soon"])
        self.assertEqual(self.names(sted=self.cellar.pk, garanti="udloebet"), [])

    def test_sorting_with_nulls_last(self):
        self.assertEqual(self.names(sorter="garanti"), ["Expired", "Soon", "Active", "NoWarranty"])
        self.assertEqual(self.names(sorter="koebt"), ["Active", "Soon", "Expired", "NoWarranty"])
        self.assertEqual(self.names(sorter="antal"), ["NoWarranty", "Soon", "Active", "Expired"])
        self.assertEqual(self.names(sorter="sted"), ["Expired", "Soon", "Active", "NoWarranty"])

    def test_unknown_values_are_harmless(self):
        for params in ({"sorter": "password; DROP"}, {"garanti": "x"}, {"sted": "' OR 1=1"}, {"sted": "99999999999999999999"}):
            with freeze_today():
                self.assertEqual(self.client.get(reverse("inventory:list"), params).status_code, 200, params)

    def test_summary_numbers_ignore_filters(self):
        with freeze_today():
            ctx = self.client.get(reverse("inventory:list"), {"sted": self.cellar.pk}).context
        self.assertEqual((ctx["total_items"], ctx["total_quantity"]), (4, 17))
        self.assertEqual((ctx["soon_count"], ctx["expired_count"]), (1, 1))

    def test_badges_and_attachment_count_render(self):
        Attachment.objects.create(item=Item.objects.get(name="Soon"), file=png())
        with freeze_today():
            page = self.client.get(reverse("inventory:list")).content.decode()
        for text in ("Udløbet", "Udløber om 16 dage", "Garanti OK", "bi-paperclip"):
            self.assertIn(text, page)


class ExportTests(InventoryTestCase):
    def rows(self, **params):
        with freeze_today():
            response = self.client.get(reverse("inventory:export"), params)
        self.assertIn("text/csv", response["Content-Type"])
        text = response.content.decode("utf-8")
        self.assertTrue(text.startswith("﻿"))
        return list(csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=";"))

    def test_export_contents_and_filters(self):
        self.client.force_login(self.anna)
        self.make("A", location="Garage", quantity=2, purchased_on=date(2025, 1, 2), warranty_expires_on=date(2026, 1, 1))
        b = self.make("B", location="Kælder")
        rows = self.rows()
        self.assertEqual(rows[1], ["A", "Garage", "2", "2025-01-02", "2026-01-01", "Udløbet", ""])
        self.assertEqual(len(rows), 3)
        self.assertEqual([r[0] for r in self.rows(sted=b.location.pk)[1:]], ["B"])

    def test_formula_injection_is_neutralised(self):
        self.client.force_login(self.anna)
        self.make("=HYPERLINK(\"http://ond.dk\")", location="+1+1", notes="@SUM(A1)")
        row = self.rows()[1]
        self.assertTrue(row[0].startswith("'=") and row[1].startswith("'+") and row[6].startswith("'@"))


class AttachmentTests(InventoryTestCase):
    def setUp(self):
        for path in self.disk_files():
            path.unlink()
        self.client.force_login(self.anna)
        self.item = self.make()
        self.url = reverse("inventory:update", args=[self.item.pk])

    def save(self, files, **extra):
        data = {"name": "Boremaskine", "quantity": 1, "new_files": files, **NO_ATTACHMENT_FORMS, **extra}
        return self.client.post(self.url, data)

    def test_upload_image_and_pdf(self):
        self.assertEqual(self.save([png("a.png"), pdf("b.pdf")]).status_code, 302)
        self.assertEqual(self.item.attachments.count(), 2)
        self.assertEqual(len(self.disk_files()), 2)
        self.assertTrue(all("kvittering" not in p.name for p in self.disk_files()))  # tilfældige filnavne

    def test_upload_on_create(self):
        response = self.client.post(reverse("inventory:create"), {"name": "Ny", "quantity": 1, "new_files": [pdf()]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Attachment.objects.count(), 1)

    def test_invalid_files_are_rejected(self):
        bad = [
            SimpleUploadedFile("fake.png", b"<?php echo 1; ?>", content_type="image/png"),
            SimpleUploadedFile("fake.pdf", b"<html><script>alert(1)</script></html>", content_type="application/pdf"),
            SimpleUploadedFile("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>1</script></svg>", content_type="image/svg+xml"),
            SimpleUploadedFile("x.exe", b"MZ\x90\x00", content_type="application/octet-stream"),
            SimpleUploadedFile("pdf-in-disguise.png", b"%PDF-1.4 hej", content_type="image/png"),
            SimpleUploadedFile("billede.pdf", png().read(), content_type="application/pdf"),
        ]
        for upload in bad:
            self.assertEqual(self.save([upload]).status_code, 400, upload.name)
        self.assertFalse(Attachment.objects.exists())
        self.assertEqual(len(self.disk_files()), 0)

    def test_too_large_is_rejected(self):
        with mock.patch("guides.validators.MAX_UPLOAD_BYTES", 10), mock.patch("inventory.validators.MAX_UPLOAD_BYTES", 10):
            self.assertEqual(self.save([png()]).status_code, 400)
            self.assertEqual(self.save([pdf()]).status_code, 400)
        self.assertFalse(Attachment.objects.exists())

    def test_max_attachments(self):
        self.assertEqual(self.save([png(f"{i}.png") for i in range(11)]).status_code, 400)
        self.assertEqual(self.save([png(f"{i}.png") for i in range(10)]).status_code, 302)
        self.assertEqual(self.save([png("en-mere.png")]).status_code, 400)

    def test_serve_image_inline_and_pdf_as_download(self):
        image = Attachment.objects.create(item=self.item, file=png())
        document = Attachment.objects.create(item=self.item, file=pdf(), label="Kvittering")
        response = self.client.get(reverse("inventory:attachment", args=[image.pk]))
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertNotIn("attachment", response.get("Content-Disposition", ""))
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", response["Content-Security-Policy"])
        b"".join(response.streaming_content)
        response = self.client.get(reverse("inventory:attachment", args=[document.pk]))
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn('attachment; filename="Kvittering.pdf"', response["Content-Disposition"])
        b"".join(response.streaming_content)

    def test_edit_label_and_delete_removes_file(self):
        keep = Attachment.objects.create(item=self.item, file=png("k.png"))
        drop = Attachment.objects.create(item=self.item, file=png("d.png"))
        dropped = drop.file.name
        data = {
            "name": "Boremaskine", "quantity": 1,
            "files-TOTAL_FORMS": "2", "files-INITIAL_FORMS": "2", "files-MIN_NUM_FORMS": "0", "files-MAX_NUM_FORMS": "1000",
            "files-0-id": keep.pk, "files-0-item": self.item.pk, "files-0-label": "Kvittering",
            "files-1-id": drop.pk, "files-1-item": self.item.pk, "files-1-label": "", "files-1-DELETE": "on",
        }
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        keep.refresh_from_db()
        self.assertEqual(keep.label, "Kvittering")
        self.assertFalse(Attachment.objects.filter(pk=drop.pk).exists())
        self.assertFalse(Attachment._meta.get_field("file").storage.exists(dropped))
        self.assertEqual(len(self.disk_files()), 1)

    def test_deleting_item_removes_files(self):
        self.save([png("a.png"), pdf()])
        self.assertEqual(len(self.disk_files()), 2)
        self.client.post(reverse("inventory:delete", args=[self.item.pk]))
        self.assertEqual(len(self.disk_files()), 0)

    def test_detail_lists_attachments(self):
        Attachment.objects.create(item=self.item, file=pdf(), label="Garantibevis")
        self.assertContains(self.client.get(reverse("inventory:detail", args=[self.item.pk])), "Garantibevis")


class ReminderTests(InventoryTestCase):
    def setUp(self):
        self.pref = ReminderPreference.objects.create(user=self.anna, enabled=True, days_before=30)
        mail.outbox.clear()

    def due(self, name, days, **kwargs):
        return self.make(name, warranty_expires_on=TODAY + timedelta(days=days), **kwargs)

    def run_reminders(self, **kwargs):
        return send_due_reminders(today=TODAY, **kwargs)

    def test_sends_one_mail_listing_items_in_window(self):
        a = self.due("Boremaskine", 5, location="Garage", quantity=2)
        b = self.due("Støvsuger", 30)
        self.due("For langt væk", 31)
        self.due("Allerede udløbet", -1)
        self.make("Uden garanti")
        self.assertEqual(self.run_reminders(), 1)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["anna@example.dk"])
        self.assertIn("2 ting", message.subject)
        self.assertIn("Boremaskine (2 stk., Garage)", message.body)
        self.assertIn("om 5 dage", message.body)
        self.assertIn("Støvsuger", message.body)
        for forbidden in ("For langt væk", "Allerede udløbet", "Uden garanti"):
            self.assertNotIn(forbidden, message.body)
        self.assertIn("/udstyr/?garanti=snart", message.body)
        self.assertIn("/udstyr/paamindelser/", message.body)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.reminded_at, b.reminded_at), (TODAY, TODAY))

    def test_single_item_subject_and_today_wording(self):
        self.due("Kaffemaskine", 0)
        self.run_reminders()
        self.assertEqual(mail.outbox[0].subject, "Garanti udløber snart: Kaffemaskine")
        self.assertIn("i dag", mail.outbox[0].body)

    def test_only_one_reminder_per_warranty(self):
        self.due("Boremaskine", 5)
        self.assertEqual(self.run_reminders(), 1)
        self.assertEqual(self.run_reminders(), 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_new_item_entering_window_triggers_new_mail(self):
        self.due("A", 5)
        self.run_reminders()
        self.due("B", 6)
        self.assertEqual(self.run_reminders(), 1)
        self.assertEqual(len(mail.outbox), 2)
        self.assertNotIn("A (", mail.outbox[1].body)

    def test_changing_warranty_date_resets_reminder(self):
        item = self.due("A", 5)
        self.run_reminders()
        item.refresh_from_db()
        item.warranty_expires_on = TODAY + timedelta(days=10)
        item.save()
        item.refresh_from_db()
        self.assertIsNone(item.reminded_at)
        self.assertEqual(self.run_reminders(), 1)

    def test_editing_other_fields_keeps_reminder_flag(self):
        item = self.due("A", 5)
        self.run_reminders()
        item.refresh_from_db()
        item.quantity = 9
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.reminded_at, TODAY)

    def test_respects_days_before_setting(self):
        self.pref.days_before = 7
        self.pref.save()
        self.due("Snart", 7)
        self.due("Senere", 8)
        self.run_reminders()
        self.assertIn("Snart", mail.outbox[0].body)
        self.assertNotIn("Senere", mail.outbox[0].body)

    def test_disabled_users_without_email_and_inactive_users_get_nothing(self):
        self.pref.enabled = False
        self.pref.save()
        self.due("A", 5)
        self.assertEqual(self.run_reminders(), 0)
        self.pref.enabled = True
        self.pref.save()
        self.anna.email = ""
        self.anna.save()
        self.assertEqual(self.run_reminders(), 0)
        self.anna.email = "anna@example.dk"
        self.anna.is_active = False
        self.anna.save()
        self.assertEqual(self.run_reminders(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_users_only_get_their_own_items(self):
        ReminderPreference.objects.create(user=self.bo, enabled=True, days_before=30)
        self.due("Annas ting", 5)
        self.due("Bos ting", 5, owner=self.bo)
        self.assertEqual(self.run_reminders(), 2)
        by_recipient = {m.to[0]: m.body for m in mail.outbox}
        self.assertIn("Annas ting", by_recipient["anna@example.dk"])
        self.assertNotIn("Bos ting", by_recipient["anna@example.dk"])
        self.assertIn("Bos ting", by_recipient["bo@example.dk"])
        self.assertNotIn("Annas ting", by_recipient["bo@example.dk"])

    def test_dry_run_sends_and_changes_nothing(self):
        item = self.due("A", 5)
        self.assertEqual(self.run_reminders(dry_run=True), 1)
        self.assertEqual(len(mail.outbox), 0)
        item.refresh_from_db()
        self.assertIsNone(item.reminded_at)

    def test_failure_for_one_user_does_not_stop_others_and_is_retried(self):
        ReminderPreference.objects.create(user=self.bo, enabled=True, days_before=30)
        a = self.due("Annas", 5)
        b = self.due("Bos", 5, owner=self.bo)
        real_send = mail.EmailMessage.send

        def flaky(message, *args, **kwargs):
            if message.to == ["anna@example.dk"]:
                raise OSError("SMTP nede")
            return real_send(message, *args, **kwargs)

        with mock.patch.object(mail.EmailMessage, "send", flaky), self.assertLogs("inventory.reminders", "ERROR"):
            self.assertEqual(self.run_reminders(), 1)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertIsNone(a.reminded_at)  # prøves igen næste gang
        self.assertEqual(b.reminded_at, TODAY)
        self.assertEqual(self.run_reminders(), 1)  # nu virker det
        self.assertEqual(len(mail.outbox), 2)

    def test_management_command(self):
        self.due("A", 5)
        out = io.StringIO()
        with freeze_today():
            call_command("send_warranty_reminders", "--dry-run", stdout=out)
            self.assertIn("1 påmindelse(r) ville blive sendt", out.getvalue())
            self.assertEqual(len(mail.outbox), 0)
            out = io.StringIO()
            call_command("send_warranty_reminders", stdout=out)
        self.assertIn("1 påmindelse(r) sendt", out.getvalue())
        self.assertEqual(len(mail.outbox), 1)


class ReminderSettingsTests(InventoryTestCase):
    def setUp(self):
        self.client.force_login(self.anna)
        mail.outbox.clear()

    def test_page_renders_and_creates_preference(self):
        self.assertEqual(self.client.get(reverse("inventory:reminders")).status_code, 200)
        self.assertFalse(ReminderPreference.objects.get(user=self.anna).enabled)

    def test_save_settings_and_email(self):
        response = self.client.post(reverse("inventory:reminders"), {"enabled": "on", "days_before": 14, "email": "ny@example.dk"})
        self.assertRedirects(response, reverse("inventory:reminders"))
        pref = ReminderPreference.objects.get(user=self.anna)
        self.assertEqual((pref.enabled, pref.days_before), (True, 14))
        self.anna.refresh_from_db()
        self.assertEqual(self.anna.email, "ny@example.dk")

    def test_enabling_requires_email(self):
        response = self.client.post(reverse("inventory:reminders"), {"enabled": "on", "days_before": 30, "email": ""})
        self.assertContains(response, "Angiv en e-mailadresse", status_code=400)
        self.assertFalse(ReminderPreference.objects.get(user=self.anna).enabled)

    def test_invalid_days_and_email_rejected(self):
        self.assertEqual(self.client.post(reverse("inventory:reminders"), {"days_before": 3, "email": "x@y.dk"}).status_code, 400)
        self.assertEqual(self.client.post(reverse("inventory:reminders"), {"days_before": 30, "email": "ikke-en-mail"}).status_code, 400)

    def test_settings_are_per_user(self):
        self.client.post(reverse("inventory:reminders"), {"enabled": "on", "days_before": 7, "email": "anna@example.dk"})
        self.client.force_login(self.bo)
        self.client.get(reverse("inventory:reminders"))
        self.assertFalse(ReminderPreference.objects.get(user=self.bo).enabled)

    def test_test_mail(self):
        response = self.client.post(reverse("inventory:reminder_test"))
        self.assertRedirects(response, reverse("inventory:reminders"))
        self.assertEqual(mail.outbox[0].to, ["anna@example.dk"])
        self.assertEqual(self.client.get(reverse("inventory:reminder_test")).status_code, 405)

    def test_test_mail_without_address_or_on_failure(self):
        self.anna.email = ""
        self.anna.save()
        self.client.post(reverse("inventory:reminder_test"))
        self.assertEqual(len(mail.outbox), 0)
        self.anna.email = "anna@example.dk"
        self.anna.save()
        with mock.patch.object(mail.EmailMessage, "send", side_effect=OSError("nede")):
            response = self.client.post(reverse("inventory:reminder_test"), follow=True)
        self.assertContains(response, "Mailen kunne ikke sendes")
