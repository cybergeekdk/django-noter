from django.db import models


class Category(models.Model):
    class Color(models.TextChoices):
        PRIMARY = "primary", "Blå"
        SECONDARY = "secondary", "Grå"
        SUCCESS = "success", "Grøn"
        DANGER = "danger", "Rød"
        WARNING = "warning", "Gul"
        INFO = "info", "Turkis"
        DARK = "dark", "Sort"

    name = models.CharField("navn", max_length=60, unique=True)
    color = models.CharField("farve", max_length=20, choices=Color.choices, default=Color.SECONDARY)

    class Meta:
        ordering = ["name"]
        verbose_name = "kategori"
        verbose_name_plural = "kategorier"

    def __str__(self):
        return self.name


class Note(models.Model):
    title = models.CharField("titel", max_length=200)
    body = models.TextField("tekst", blank=True)
    category = models.ForeignKey(
        Category,
        verbose_name="kategori",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notes",
    )
    created = models.DateTimeField("oprettet", auto_now_add=True)

    class Meta:
        ordering = ["-created"]
        verbose_name = "note"
        verbose_name_plural = "noter"

    def __str__(self):
        return self.title
