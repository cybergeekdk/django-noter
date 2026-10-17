from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
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


class CheapPowerAlert(models.Model):
    """Brugerens indstillinger for besked, når strømmen er billig."""

    HOURS_CHOICES = [(1, "mindst 1 time i træk"), (2, "mindst 2 timer i træk"), (3, "mindst 3 timer i træk"), (4, "mindst 4 timer i træk")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cheap_alert")
    enabled = models.BooleanField("send mig besked om billig og dyr strøm", default=False)
    area = models.CharField("prisområde", max_length=3, choices=SpotPrice.Area.choices, default=SpotPrice.Area.DK1)
    threshold_ore = models.DecimalField(
        "billig er under",
        max_digits=6,
        decimal_places=2,
        default=40,
        validators=[MinValueValidator(-100), MaxValueValidator(1000)],
        help_text="øre/kWh. Negative priser betyder, at man får penge for at bruge strøm.",
    )
    incl_vat = models.BooleanField("grænsen er inkl. moms", default=True)
    min_hours = models.PositiveSmallIntegerField("varighed", choices=HOURS_CHOICES, default=1)
    notify_cheap = models.BooleanField("besked om billige perioder", default=True)
    warn_expensive = models.BooleanField("advar også om dyre perioder", default=False)
    expensive_ore = models.DecimalField(
        "dyr er over",
        max_digits=6,
        decimal_places=2,
        default=100,
        validators=[MinValueValidator(-100), MaxValueValidator(1000)],
        help_text="øre/kWh (samme moms-valg som grænsen for billig).",
    )
    notify_email = models.BooleanField("send e-mail", default=True)
    notify_phone = models.BooleanField("send besked til telefonen (ntfy)", default=False)
    ntfy_topic = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        verbose_name = "prisbesked"
        verbose_name_plural = "prisbeskeder"
        constraints = [
            models.UniqueConstraint(fields=["ntfy_topic"], condition=~models.Q(ntfy_topic=""), name="unique_ntfy_topic"),
        ]

    def __str__(self):
        return f"{self.user} ({'til' if self.enabled else 'fra'}, {self.area} < {self.threshold_ore})"


class AlertLog(models.Model):
    """Husker, at en dag er vurderet for en bruger, så der højst sendes én besked pr. dag."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="alert_logs")
    date = models.DateField()
    sent = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "date"], name="unique_alertlog_user_date")]
        verbose_name = "beskedlog"
        verbose_name_plural = "beskedlog"

    def __str__(self):
        return f"{self.user} {self.date} ({'sendt' if self.sent else 'ingen billige perioder'})"
