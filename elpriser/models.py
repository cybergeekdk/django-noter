from django.db import models


class SpotPrice(models.Model):
    """Day-ahead-spotpris for ét tidsinterval i ét prisområde (DK1 = Vestdanmark, DK2 = Østdanmark).

    `time` er intervallets start i UTC. Opløsningen (time/kvarter) gemmes ikke: den aflæses af data,
    så koden virker uanset hvad Energinet leverer.
    """

    class Area(models.TextChoices):
        DK1 = "DK1", "DK1 – Vestdanmark"
        DK2 = "DK2", "DK2 – Østdanmark"

    area = models.CharField("prisområde", max_length=3, choices=Area.choices)
    time = models.DateTimeField("tidspunkt (start, UTC)")
    price_dkk_mwh = models.DecimalField("pris (DKK/MWh)", max_digits=12, decimal_places=4)
    price_eur_mwh = models.DecimalField("pris (EUR/MWh)", max_digits=12, decimal_places=4, null=True, blank=True)
    updated = models.DateTimeField("opdateret", auto_now=True)

    class Meta:
        ordering = ["time", "area"]
        verbose_name = "spotpris"
        verbose_name_plural = "spotpriser"
        constraints = [models.UniqueConstraint(fields=["area", "time"], name="unique_spotprice_area_time")]
        indexes = [models.Index(fields=["area", "time"], name="spotprice_area_time_idx")]

    def __str__(self):
        return f"{self.area} {self.time:%Y-%m-%d %H:%M} UTC: {self.price_dkk_mwh} DKK/MWh"


class FetchRun(models.Model):
    """Log over hentninger fra Energinet. Bruges til «sidst opdateret» og til fejlfinding."""

    started = models.DateTimeField(auto_now_add=True)
    finished = models.DateTimeField(null=True, blank=True)
    ok = models.BooleanField(default=False)
    rows_new = models.PositiveIntegerField(default=0)
    rows_updated = models.PositiveIntegerField(default=0)
    message = models.TextField(blank=True)

    class Meta:
        ordering = ["-started"]
        verbose_name = "hentning"
        verbose_name_plural = "hentninger"

    def __str__(self):
        return f"{self.started:%Y-%m-%d %H:%M} {'OK' if self.ok else 'FEJL'}"
