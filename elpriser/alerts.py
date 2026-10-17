"""Besked om billig strøm.

`send_due_alerts` køres af cron (typisk lige efter hver prishentning). For hver bruger vurderes
i dag og i morgen én gang: er der perioder under brugerens grænse, sendes én mail; ellers sker intet.
Day-ahead-priser ændrer sig ikke, når de først er offentliggjort, så én vurdering pr. dag er nok.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.urls import reverse
from django.utils import timezone

from . import analysis
from .models import AlertLog, CheapPowerAlert
from .service import DK_TZ

logger = logging.getLogger(__name__)

MIN_COVERAGE = 0.95  # andel af dagens intervaller, der skal være hentet, før dagen vurderes


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime
    avg: float  # øre/kWh (med/uden moms efter brugerens valg)
    low: float

    @property
    def hours(self):
        return (self.end - self.start).total_seconds() / 3600


def fmt(value):
    return f"{value:.1f}".replace(".", ",")


def find_periods(points, step, threshold, vat, min_minutes):
    """Sammenhængende perioder, hvor prisen er højst `threshold` øre/kWh, og som varer mindst `min_minutes`."""
    periods, run = [], []

    def close():
        if run and len(run) * step >= min_minutes:
            values = [v for _, v in run]
            periods.append(Period(run[0][0], run[-1][0] + timedelta(minutes=step), sum(values) / len(values), min(values)))

    for moment, price in points:
        value = analysis.to_ore_kwh(price, vat)
        if value > threshold:
            close()
            run = []
        elif run and moment - run[-1][0] != timedelta(minutes=step):  # hul i data: ny periode
            close()
            run = [(moment, value)]
        else:
            run.append((moment, value))
    close()
    return periods


def day_periods(alert, day):
    """(perioder, klar). `klar` er False, hvis dagens priser mangler eller kun er delvist hentet."""
    points = analysis.day_points(alert.area, day)
    if not points:
        return [], False
    step = analysis.step_minutes(points)
    start, end = analysis.day_bounds(day)
    expected = (end - start).total_seconds() / 60 / step
    if len(points) < expected * MIN_COVERAGE:
        return [], False
    periods = find_periods(points, step, float(alert.threshold_ore), alert.incl_vat, alert.min_hours * 60)
    return periods, True


def site_url(path):
    return settings.SITE_URL.rstrip("/") + path


def span(period):
    return f"{period.start.astimezone(DK_TZ):%H:%M}–{period.end.astimezone(DK_TZ):%H:%M}"


def day_word(day, today):
    return "i dag" if day == today else "i morgen"


def build_message(alert, days, today):
    """Én mail for alle de dage (højst i dag og i morgen), der har billige perioder. `days`: [(dag, perioder)]."""
    user = alert.user
    words = " og ".join(day_word(day, today) for day, _ in days)
    every = [p for _, periods in days for p in periods]
    best = min(every, key=lambda p: p.avg)
    if len(every) == 1:
        subject = f"Billig strøm {words} ({alert.area}): kl. {span(best)} · {fmt(best.avg)} øre/kWh"
    else:
        subject = f"Billig strøm {words} ({alert.area}): {len(every)} perioder, billigst kl. {span(best)}"
    vat = "inkl. moms" if alert.incl_vat else "ekskl. moms"
    sections = []
    for day, periods in days:
        lines = [
            f"- kl. {span(p)} ({f'{p.hours:g}'.replace('.', ',')} timer): gennemsnit {fmt(p.avg)} øre/kWh, laveste {fmt(p.low)}"
            for p in periods
        ]
        link = site_url(reverse("elpriser:index") + f"?omraade={alert.area}&dato={day.isoformat()}")
        sections.append(f"{day_word(day, today).capitalize()} ({day:%d/%m}):\n" + "\n".join(lines) + f"\n\nSe hele dagen: {link}")
    body = (
        f"Hej {user.get_username()},\n\n"
        f"Strømmen er billig i {alert.area} (under {fmt(float(alert.threshold_ore))} øre/kWh {vat}):\n\n"
        + "\n\n".join(sections)
        + "\n\nPriserne er spotpriser uden nettarif, afgifter og abonnement.\n"
        f"Du får denne besked, fordi du har slået den til. Ændre eller slå den fra: {site_url(reverse('elpriser:alerts'))}\n"
    )
    return EmailMessage(subject, body, to=[user.email])


def due_alerts(now=None):
    """Yield (alert, [(dag, perioder)]) for hver bruger med dage, der skal vurderes nu.

    Dage uden (fuldstændige) priser springes over og vurderes næste gang. `perioder` kan være tom.
    """
    now = now or timezone.now()
    today = now.astimezone(DK_TZ).date()
    alerts = (
        CheapPowerAlert.objects.filter(enabled=True, user__is_active=True).exclude(user__email="").select_related("user")
    )
    for alert in alerts:
        done = set(AlertLog.objects.filter(user=alert.user, date__gte=today).values_list("date", flat=True))
        evaluated = []
        for day in (today, today + timedelta(days=1)):
            if day in done:
                continue
            periods, ready = day_periods(alert, day)
            if not ready:
                continue
            if day == today:  # perioder, der allerede er slut, er ikke interessante
                periods = [p for p in periods if p.end > now]
            evaluated.append((day, periods))
        if evaluated:
            yield alert, evaluated


def send_due_alerts(now=None, dry_run=False):
    """Sender de forfaldne beskeder (højst én mail pr. bruger pr. kørsel) og returnerer antal mails.

    Ved dry_run sendes og gemmes intet; tallet er antal mails, der ville blive sendt.
    """
    now = now or timezone.now()
    today = now.astimezone(DK_TZ).date()
    sent = 0
    for alert, evaluated in due_alerts(now):
        with_periods = [(day, periods) for day, periods in evaluated if periods]
        if dry_run:
            sent += bool(with_periods)
            continue
        if with_periods:
            try:
                build_message(alert, with_periods, today).send()
            except Exception:  # én brugers fejl må ikke stoppe de andre; prøves igen ved næste kørsel
                logger.exception("Kunne ikke sende prisbesked til bruger %s", alert.user_id)
                continue
            sent += 1
        for day, periods in evaluated:
            AlertLog.objects.get_or_create(user=alert.user, date=day, defaults={"sent": bool(periods)})
    return sent


def forget_pending_days(user, today=None):
    """Efter ændrede indstillinger skal i dag og i morgen vurderes igen."""
    today = today or timezone.localdate()
    AlertLog.objects.filter(user=user, date__gte=today).delete()
