import shutil
import tempfile
from io import BytesIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template import Context, Template
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from .models import Guide, Section, SectionImage

User = get_user_model()
TMP = tempfile.mkdtemp(prefix="guides-test-")


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def make_image(name="foto.png", fmt="PNG", size=(40, 30)):
    buffer = BytesIO()
    Image.new("RGB", size, "tomato").save(buffer, format=fmt)
    content_type = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif"}[fmt]
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=content_type)


@override_settings(PRIVATE_MEDIA_ROOT=TMP)
class GuideTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna", password="pw")
        cls.bo = User.objects.create_user("bo", password="pw")
        cls.boss = User.objects.create_superuser("boss", password="pw")

    def disk_files(self):
        return [p for p in Path(TMP).rglob("*") if p.is_file()]


class PrivacyTests(GuideTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.guide = Guide.objects.create(owner=cls.anna, title="Annas hemmelige guide", cover=make_image("c.png"))
        cls.section = Section.objects.create(guide=cls.guide, title="Afsnit", body="hemmelig tekst")
        cls.image = SectionImage.objects.create(section=cls.section, image=make_image())

    def urls(self):
        g, s, i = self.guide.pk, self.section.pk, self.image.pk
        return {
            "detail": reverse("guides:detail", args=[g]),
            "update": reverse("guides:update", args=[g]),
            "delete": reverse("guides:delete", args=[g]),
            "cover": reverse("guides:cover", args=[g]),
            "section_create": reverse("guides:section_create", args=[g]),
            "section_update": reverse("guides:section_update", args=[s]),
            "section_delete": reverse("guides:section_delete", args=[s]),
            "image": reverse("guides:image", args=[i]),
        }

    def test_anonymous_is_sent_to_login(self):
        for name, url in {**self.urls(), "list": reverse("guides:list"), "new": reverse("guides:create")}.items():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, name)
            self.assertIn(reverse("accounts:login"), response["Location"], name)

    def test_other_users_get_404_on_everything(self):
        for who in (self.bo, self.boss):  # også super admin
            self.client.force_login(who)
            for name, url in self.urls().items():
                self.assertEqual(self.client.get(url).status_code, 404, f"{who} {name}")

    def test_other_users_cannot_modify(self):
        self.client.force_login(self.bo)
        self.assertEqual(self.client.post(self.urls()["delete"]).status_code, 404)
        self.assertEqual(self.client.post(self.urls()["section_delete"]).status_code, 404)
        self.assertEqual(self.client.post(reverse("guides:section_move", args=[self.section.pk]), {"direction": "up"}).status_code, 404)
        self.assertEqual(self.client.post(self.urls()["section_create"], {"title": "Snyd"}).status_code, 404)
        self.assertEqual(self.client.post(self.urls()["update"], {"title": "Hacket"}).status_code, 404)
        self.guide.refresh_from_db()
        self.assertEqual(self.guide.title, "Annas hemmelige guide")
        self.assertEqual(self.guide.sections.count(), 1)

    def test_list_only_shows_own_guides(self):
        Guide.objects.create(owner=self.bo, title="Bos guide")
        self.client.force_login(self.bo)
        response = self.client.get(reverse("guides:list"))
        self.assertContains(response, "Bos guide")
        self.assertNotContains(response, "Annas hemmelige guide")

    def test_search_does_not_leak_other_users_content(self):
        self.client.force_login(self.bo)
        response = self.client.get(reverse("guides:list"), {"q": "hemmelig"})
        self.assertNotContains(response, "Annas hemmelige guide")

    def test_owner_can_see_everything(self):
        self.client.force_login(self.anna)
        self.assertContains(self.client.get(self.urls()["detail"]), "hemmelig tekst")
        for name in ("cover", "image"):
            self.assertEqual(self.client.get(self.urls()[name]).status_code, 200, name)

    def test_images_are_not_publicly_reachable(self):
        """Ingen offentlig URL til filerne, og filerne ligger uden for static/media."""
        stored = self.image.image.name
        self.assertTrue(stored.startswith("guides/"))
        with self.assertRaises(ValueError):
            self.image.image.url
        self.assertEqual(self.client.get(f"/media/{stored}").status_code, 404)
        self.assertEqual(self.client.get(f"/static/{stored}").status_code, 404)

    def test_image_response_headers(self):
        self.client.force_login(self.anna)
        response = self.client.get(self.urls()["image"])
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("sandbox", response["Content-Security-Policy"])
        b"".join(response.streaming_content)  # lukker filen

    def test_menu_link_only_when_logged_in(self):
        self.assertNotContains(self.client.get(reverse("pages:home")), reverse("guides:list"))
        self.client.force_login(self.bo)
        self.assertContains(self.client.get(reverse("pages:home")), reverse("guides:list"))


class GuideCrudTests(GuideTestCase):
    def setUp(self):
        self.client.force_login(self.anna)

    def test_create_guide_sets_owner_and_redirects_to_first_section(self):
        response = self.client.post(
            reverse("guides:create"),
            {"title": "Min guide", "summary": "Kort", "owner": self.bo.pk, "cover": make_image("forside.png")},
        )
        guide = Guide.objects.get(title="Min guide")
        self.assertEqual(guide.owner, self.anna)  # owner kan ikke sættes udefra
        self.assertRedirects(response, reverse("guides:section_create", args=[guide.pk]))
        self.assertTrue(guide.cover)
        self.assertNotIn("forside", guide.cover.name)  # brugerens filnavn bruges ikke

    def test_title_required(self):
        self.assertEqual(self.client.post(reverse("guides:create"), {"title": ""}).status_code, 200)
        self.assertFalse(Guide.objects.exists())

    def test_sections_are_appended_and_can_be_moved(self):
        guide = Guide.objects.create(owner=self.anna, title="G")
        url = reverse("guides:section_create", args=[guide.pk])
        for title in ("A", "B", "C"):
            self.client.post(url, {"title": title, "body": title})
        self.assertEqual([s.title for s in guide.sections.all()], ["A", "B", "C"])
        b = guide.sections.get(title="B")
        move = reverse("guides:section_move", args=[b.pk])
        self.client.post(move, {"direction": "up"})
        self.assertEqual([s.title for s in guide.sections.all()], ["B", "A", "C"])
        self.client.post(move, {"direction": "up"})  # allerede øverst: ingen ændring
        self.assertEqual([s.title for s in guide.sections.all()], ["B", "A", "C"])
        self.client.post(move, {"direction": "down"})
        self.assertEqual([s.title for s in guide.sections.all()], ["A", "B", "C"])
        self.assertEqual(self.client.get(move).status_code, 405)

    def test_detail_renders_sections_in_order_with_toc(self):
        guide = Guide.objects.create(owner=self.anna, title="G")
        Section.objects.create(guide=guide, title="Første", position=0)
        Section.objects.create(guide=guide, title="Anden", position=1)
        response = self.client.get(guide.get_absolute_url())
        self.assertContains(response, "#afsnit-")
        self.assertLess(response.content.decode().index("Første"), response.content.decode().index("Anden"))

    def test_search_finds_guide_by_section_text(self):
        guide = Guide.objects.create(owner=self.anna, title="Opskrifter")
        Section.objects.create(guide=guide, title="Brød", body="brug 500 g hvedemel")
        response = self.client.get(reverse("guides:list"), {"q": "hvedemel"})
        self.assertContains(response, "Opskrifter")

    def test_delete_guide(self):
        guide = Guide.objects.create(owner=self.anna, title="G")
        url = reverse("guides:delete", args=[guide.pk])
        self.client.get(url)
        self.assertTrue(Guide.objects.filter(pk=guide.pk).exists())  # GET sletter ikke
        self.assertRedirects(self.client.post(url), reverse("guides:list"))
        self.assertFalse(Guide.objects.filter(pk=guide.pk).exists())


class UploadTests(GuideTestCase):
    def setUp(self):
        # Filer rulles ikke tilbage med databasen, så start med en tom mappe.
        for path in self.disk_files():
            path.unlink()
        self.client.force_login(self.anna)
        self.guide = Guide.objects.create(owner=self.anna, title="G")
        self.url = reverse("guides:section_create", args=[self.guide.pk])

    def post(self, files, **extra):
        return self.client.post(self.url, {"title": "S", "body": "x", "new_images": files, **extra})

    def test_multiple_images_upload(self):
        response = self.post([make_image("a.png"), make_image("b.jpg", "JPEG"), make_image("c.gif", "GIF")])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SectionImage.objects.count(), 3)
        self.assertEqual(len(self.disk_files()), 3)

    def test_fake_image_is_rejected(self):
        fake = SimpleUploadedFile("virus.png", b"<?php echo 1; ?>", content_type="image/png")
        response = self.post([fake])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(SectionImage.objects.count(), 0)
        self.assertFalse(Section.objects.exists())

    def test_svg_is_rejected(self):
        svg = SimpleUploadedFile("x.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', content_type="image/svg+xml")
        self.assertEqual(self.post([svg]).status_code, 400)
        self.assertEqual(SectionImage.objects.count(), 0)

    def test_real_image_with_wrong_format_is_rejected(self):
        buffer = BytesIO()
        Image.new("RGB", (10, 10)).save(buffer, format="BMP")
        upload = SimpleUploadedFile("x.png", buffer.getvalue(), content_type="image/png")
        self.assertEqual(self.post([upload]).status_code, 400)

    def test_too_large_is_rejected(self):
        with mock.patch("guides.validators.MAX_UPLOAD_BYTES", 10):
            self.assertEqual(self.post([make_image()]).status_code, 400)
        self.assertEqual(SectionImage.objects.count(), 0)

    def test_html_file_with_image_extension_is_rejected(self):
        html = SimpleUploadedFile("page.jpg", b"<html><script>alert(1)</script></html>", content_type="image/jpeg")
        self.assertEqual(self.post([html]).status_code, 400)

    def test_too_many_images_rejected(self):
        files = [make_image(f"{i}.png") for i in range(21)]
        self.assertEqual(self.post(files).status_code, 400)

    def test_edit_caption_and_delete_image_removes_file(self):
        section = Section.objects.create(guide=self.guide, title="S")
        keep = SectionImage.objects.create(section=section, image=make_image("k.png"))
        drop = SectionImage.objects.create(section=section, image=make_image("d.png"))
        dropped_name = drop.image.name
        url = reverse("guides:section_update", args=[section.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        data = {
            "title": "S", "body": "",
            "images-TOTAL_FORMS": "2", "images-INITIAL_FORMS": "2", "images-MIN_NUM_FORMS": "0", "images-MAX_NUM_FORMS": "1000",
            "images-0-id": keep.pk, "images-0-section": section.pk, "images-0-caption": "Ny tekst",
            "images-1-id": drop.pk, "images-1-section": section.pk, "images-1-caption": "", "images-1-DELETE": "on",
        }
        self.assertEqual(self.client.post(url, data).status_code, 302)
        keep.refresh_from_db()
        self.assertEqual(keep.caption, "Ny tekst")
        self.assertFalse(SectionImage.objects.filter(pk=drop.pk).exists())
        self.assertFalse(SectionImage._meta.get_field("image").storage.exists(dropped_name))
        self.assertEqual(len(self.disk_files()), 1)

    def test_cannot_attach_image_form_from_another_users_section(self):
        other_guide = Guide.objects.create(owner=self.bo, title="Bo")
        other_section = Section.objects.create(guide=other_guide, title="S")
        victim = SectionImage.objects.create(section=other_section, image=make_image())
        mine = Section.objects.create(guide=self.guide, title="Mit")
        url = reverse("guides:section_update", args=[mine.pk])
        data = {
            "title": "Mit", "body": "",
            "images-TOTAL_FORMS": "1", "images-INITIAL_FORMS": "1", "images-MIN_NUM_FORMS": "0", "images-MAX_NUM_FORMS": "1000",
            "images-0-id": victim.pk, "images-0-section": mine.pk, "images-0-caption": "overtaget", "images-0-DELETE": "on",
        }
        self.client.post(url, data)
        self.assertTrue(SectionImage.objects.filter(pk=victim.pk).exists())

    def test_deleting_section_and_guide_removes_files(self):
        self.post([make_image("a.png"), make_image("b.png")])
        self.guide.cover = make_image("cover.png")
        self.guide.save()
        self.assertEqual(len(self.disk_files()), 3)
        section = self.guide.sections.get()
        self.client.post(reverse("guides:section_delete", args=[section.pk]))
        self.assertEqual(len(self.disk_files()), 1)  # kun forsiden er tilbage
        self.client.post(reverse("guides:delete", args=[self.guide.pk]))
        self.assertEqual(len(self.disk_files()), 0)

    def test_replacing_cover_removes_old_file(self):
        self.client.post(reverse("guides:update", args=[self.guide.pk]), {"title": "G", "cover": make_image("1.png")})
        self.client.post(reverse("guides:update", args=[self.guide.pk]), {"title": "G", "cover": make_image("2.png")})
        self.assertEqual(len(self.disk_files()), 1)

    def test_clearing_cover_removes_file(self):
        self.client.post(reverse("guides:update", args=[self.guide.pk]), {"title": "G", "cover": make_image("1.png")})
        self.client.post(reverse("guides:update", args=[self.guide.pk]), {"title": "G", "cover-clear": "on"})
        self.assertEqual(len(self.disk_files()), 0)


class MarkdownSafetyTests(TestCase):
    def render(self, text):
        return Template("{% load guides_extras %}{{ text|markdown_safe }}").render(Context({"text": text}))

    def test_formatting_works(self):
        html = self.render("# Titel\n\n**fed** og `kode`\n\n- a\n- b")
        self.assertIn("<h3>Titel</h3>", html)
        self.assertIn("<strong>fed</strong>", html)
        self.assertIn("<li>a</li>", html)

    def test_headings_never_outrank_section_title(self):
        html = self.render("# a\n\n## b\n\n###### f")
        self.assertNotIn("<h1", html)
        self.assertNotIn("<h2", html)

    def test_scripts_and_handlers_are_stripped(self):
        html = self.render('<script>alert(1)</script><img src=x onerror=alert(1)><iframe src="//evil"></iframe><b onclick="x()">hej</b>')
        for bad in ("<script", "onerror", "<img", "<iframe", "onclick"):
            self.assertNotIn(bad, html)

    def test_javascript_links_are_stripped(self):
        html = self.render("[klik](javascript:alert(1)) og [ok](https://eksempel.dk)")
        self.assertNotIn("javascript:", html)
        self.assertIn('href="https://eksempel.dk"', html)
        self.assertIn("noopener", html)

    def test_markdown_images_are_not_allowed(self):
        self.assertNotIn("<img", self.render("![x](https://tracker.example/pixel.png)"))
