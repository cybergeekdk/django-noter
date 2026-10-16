from django.db import models


class Note(models.Model):
    title = models.CharField("titel", max_length=200)
    body = models.TextField("tekst", blank=True)
    created = models.DateTimeField("oprettet", auto_now_add=True)

    class Meta:
        ordering = ["-created"]
        verbose_name = "note"
        verbose_name_plural = "noter"

    def __str__(self):
        return self.title
