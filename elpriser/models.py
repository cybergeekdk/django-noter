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
    include_grid = models.BooleanField("medregn nettarif og tillæg", default=False)
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


class GridCompany(models.Model):
    """Et netselskab (fra Energinets prisliste). `gln` er netselskabets unikke nummer i Datahub."""

    gln = models.CharField("GLN", max_length=13, unique=True)
    name = models.CharField("navn", max_length=200)

    class Meta:
        ordering = ["name"]
        verbose_name = "netselskab"
        verbose_name_plural = "netselskaber"

    def __str__(self):
        return self.name


class GridTariff(models.Model):
    """En nettarif fra Energinets prisliste, gyldig i en periode, med en pris for hver time på døgnet.

    Priserne gemmes som i Datahub: DKK/kWh uden moms. Gyldighed er danske kalenderdage; sommer- og
    vintertariffer er to rækker med hver sin periode.
    """

    gln = models.CharField("netselskabets GLN", max_length=13, db_index=True)
    code = models.CharField("tarifkode", max_length=40)
    name = models.CharField("navn", max_length=300, blank=True)
    valid_from = models.DateField("gælder fra")
    valid_to = models.DateField("gælder til (ekskl.)", null=True, blank=True)
    hour_prices = models.JSONField("pris pr. time (DKK/kWh, 24 værdier)")
    fetched = models.DateTimeField("hentet", auto_now=True)

    class Meta:
        ordering = ["gln", "code", "-valid_from"]
        verbose_name = "nettarif"
        verbose_name_plural = "nettariffer"
        constraints = [models.UniqueConstraint(fields=["gln", "code", "valid_from"], name="unique_gridtariff_period")]

    def __str__(self):
        return f"{self.gln} {self.code} fra {self.valid_from}"


class GridSettings(models.Model):
    """Brugerens nettarif og øvrige tillæg, så graferne og beskederne kan vise prisen inkl. nettarif."""

    class Source(models.TextChoices):
        DATAHUB = "datahub", "Hent fra Energinet (vælg netselskab)"
        MANUAL = "manual", "Tast timepriser ind selv"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="grid_settings")
    source = models.CharField("kilde", max_length=10, choices=Source.choices, default=Source.DATAHUB)
    company = models.ForeignKey(GridCompany, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tariff_code = models.CharField("tarif", max_length=40, blank=True)
    manual_hours = models.JSONField("egne timepriser (øre/kWh)", default=list, blank=True)
    elafgift_ore = models.DecimalField("elafgift", max_digits=7, decimal_places=3, default=0, validators=[MinValueValidator(0), MaxValueValidator(1000)])
    energinet_ore = models.DecimalField("Energinets tariffer", max_digits=7, decimal_places=3, default=0, validators=[MinValueValidator(0), MaxValueValidator(1000)])
    supplier_ore = models.DecimalField("leverandørtillæg", max_digits=7, decimal_places=3, default=0, validators=[MinValueValidator(0), MaxValueValidator(1000)])

    class Meta:
        verbose_name = "nettarif-indstilling"
        verbose_name_plural = "nettarif-indstillinger"

    def __str__(self):
        return f"{self.user} ({self.source})"

    @property
    def fixed_ore(self):
        """Faste tillæg pr. kWh (øre, uden moms), som ikke afhænger af tidspunktet."""
        return float(self.elafgift_ore + self.energinet_ore + self.supplier_ore)

    @property
    def configured(self):
        if self.source == self.Source.MANUAL:
            return len(self.manual_hours) == 24
        return bool(self.company_id and self.tariff_code)
