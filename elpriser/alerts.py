"""Besked om billig (og evt. dyr) strøm på e-mail og/eller telefon.

`send_due_alerts` køres af cron (typisk lige efter hver prishentning). For hver bruger vurderes
i dag og i morgen én gang: er der billige (eller dyre) perioder efter brugerens indstillinger, sendes
én besked; ellers sker intet. Day-ahead-priser ændrer sig ikke, når de først er offentliggjort,
så én vurdering pr. dag er nok.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from . import analysis, push
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
    high: float = 0.0

    @property
    def hours(self):
        return (self.end - self.start).total_seconds() / 3600


@dataclass
class DayReport:
    day: date
    cheap: list = field(default_factory=list)
    expensive: list = field(default_factory=list)

    @property
    def has_any(self):
        return bool(self.cheap or self.expensive)


def fmt(value):
    return f"{value:.1f}".replace(".", ",")


def find_periods(points, step, threshold, vat, min_minutes, above=False):
    """Sammenhængende perioder (mindst `min_minutes` lange).

    Som standard billige perioder (pris ≤ `threshold` øre/kWh); med `above=True` dyre perioder (pris > `threshold`).
    """
    periods, run = [], []

    def close():
        if run and len(run) * step >= min_minutes:
            values = [v for _, v in run]
            periods.append(
                Period(run[0][0], run[-1][0] + timedelta(minutes=step), sum(values) / len(values), min(values), max(values))
            )

    for moment, price in points:
        value = analysis.to_ore_kwh(price, vat)
        if (value <= threshold) if not above else (value > threshold):
            if run and moment - run[-1][0] != timedelta(minutes=step):  # hul i data: ny periode
                close()
                run = []
            run.append((moment, value))
        else:
            close()
            run = []
    close()
    return periods


def day_report(alert, day):
    """`DayReport` for dagen efter brugerens indstillinger, eller None, hvis dagens priser mangler/er ufuldstændige."""
    points = analysis.day_points(alert.area, day)
    if not points:
        return None
    step = analysis.step_minutes(points)
    start, end = analysis.day_bounds(day)
    expected = (end - start).total_seconds() / 60 / step
    if len(points) < expected * MIN_COVERAGE:
        return None
    minutes = alert.min_hours * 60
    report = DayReport(day)
    if alert.notify_cheap:
        report.cheap = find_periods(points, step, float(alert.threshold_ore), alert.incl_vat, minutes)
    if alert.warn_expensive:
        report.expensive = find_periods(points, step, float(alert.expensive_ore), alert.incl_vat, minutes, above=True)
    return report


def site_url(path):
    return settings.SITE_URL.rstrip("/") + path


def span(period):
    return f"{period.start.astimezone(DK_TZ):%H:%M}–{period.end.astimezone(DK_TZ):%H:%M}"


def day_word(day, today):
    return "i dag" if day == today else "i morgen"


def hours_text(period):
    return f"{period.hours:g}".replace(".", ",")


def subject_for(alert, reports, today):
    words = " og ".join(day_word(r.day, today) for r in reports)
    cheap = [p for r in reports for p in r.cheap]
    expensive = [p for r in reports for p in r.expensive]
    best = min(cheap, key=lambda p: p.avg) if cheap else None
    worst = max(expensive, key=lambda p: p.avg) if expensive else None
    if best and not worst:
        if len(cheap) == 1:
            return f"Billig strøm {words} ({alert.area}): kl. {span(best)} · {fmt(best.avg)} øre/kWh"
        return f"Billig strøm {words} ({alert.area}): {len(cheap)} perioder, billigst kl. {span(best)}"
    if worst and not best:
        if len(expensive) == 1:
            return f"Dyr strøm {words} ({alert.area}): kl. {span(worst)} · {fmt(worst.avg)} øre/kWh"
        return f"Dyr strøm {words} ({alert.area}): {len(expensive)} perioder, dyrest kl. {span(worst)}"
    return f"Billig og dyr strøm {words} ({alert.area}): billigst kl. {span(best)}, dyrest kl. {span(worst)}"


def link_for(alert, day):
    return site_url(reverse("elpriser:index") + f"?omraade={alert.area}&dato={day.isoformat()}")


def email_for(alert, reports, today):
    vat = "inkl. moms" if alert.incl_vat else "ekskl. moms"
    sections = []
    for report in reports:
        lines = []
        if report.cheap:
            lines.append(f"Billigt (under {fmt(float(alert.threshold_ore))} øre/kWh {vat}):")
            lines += [f"- kl. {span(p)} ({hours_text(p)} timer): gennemsnit {fmt(p.avg)} øre/kWh, laveste {fmt(p.low)}" for p in report.cheap]
        if report.expensive:
            lines.append(f"Dyrt (over {fmt(float(alert.expensive_ore))} øre/kWh {vat}):")
            lines += [f"- kl. {span(p)} ({hours_text(p)} timer): gennemsnit {fmt(p.avg)} øre/kWh, højeste {fmt(p.high)}" for p in report.expensive]
        header = f"{day_word(report.day, today).capitalize()} ({report.day:%d/%m}):"
        sections.append(header + "\n" + "\n".join(lines) + f"\n\nSe hele dagen: {link_for(alert, report.day)}")
    body = (
        f"Hej {alert.user.get_username()},\n\n"
        f"Her er prisbeskeden for {alert.area}:\n\n"
        + "\n\n".join(sections)
        + "\n\nPriserne er spotpriser uden nettarif, afgifter og abonnement.\n"
        f"Du får denne besked, fordi du har slået den til. Ændre eller slå den fra: {site_url(reverse('elpriser:alerts'))}\n"
    )
    return EmailMessage(subject_for(alert, reports, today), body, to=[alert.user.email])


def push_text_for(alert, reports, today):
    """Kort tekst til telefonen."""
    lines = []
    for report in reports:
        parts = []
        if report.cheap:
            parts.append("billigt " + ", ".join(f"kl. {span(p)} ({fmt(p.avg)} øre)" for p in report.cheap))
        if report.expensive:
            parts.append("dyrt " + ", ".join(f"kl. {span(p)} ({fmt(p.avg)} øre)" for p in report.expensive))
        lines.append(f"{day_word(report.day, today).capitalize()}: " + "; ".join(parts))
    return "\n".join(lines)


def eligible_alerts():
    wants_email = Q(notify_email=True) & ~Q(user__email="")
    wants_phone = Q(notify_phone=True) & ~Q(ntfy_topic="")
    wants_something = Q(notify_cheap=True) | Q(warn_expensive=True)
    return (
        CheapPowerAlert.objects.filter(enabled=True, user__is_active=True)
        .filter(wants_something)
        .filter(wants_email | wants_phone)
        .select_related("user")
    )


def due_alerts(now=None):
    """Yield (alert, [DayReport]) for hver bruger med dage, der skal vurderes nu.

    Dage uden (fuldstændige) priser springes over og vurderes næste gang. Rapporter kan være tomme.
    """
    now = now or timezone.now()
    today = now.astimezone(DK_TZ).date()
    for alert in eligible_alerts():
        done = set(AlertLog.objects.filter(user=alert.user, date__gte=today).values_list("date", flat=True))
        reports = []
        for day in (today, today + timedelta(days=1)):
            if day in done:
                continue
            report = day_report(alert, day)
            if report is None:
                continue
            if day == today:  # perioder, der allerede er slut, er ikke interessante
                report.cheap = [p for p in report.cheap if p.end > now]
                report.expensive = [p for p in report.expensive if p.end > now]
            reports.append(report)
        if reports:
            yield alert, reports


def deliver(alert, reports, today):
    """Sender på de valgte kanaler. Returnerer antal kanaler, der lykkedes; fejl logges og stopper ikke de andre."""
    delivered = 0
    if alert.notify_email and alert.user.email:
        try:
            email_for(alert, reports, today).send()
            delivered += 1
        except Exception:
            logger.exception("Kunne ikke sende prisbesked på e-mail til bruger %s", alert.user_id)
    if alert.notify_phone and alert.ntfy_topic:
        try:
            push.send_ntfy(
                alert.ntfy_topic,
                subject_for(alert, reports, today),
                push_text_for(alert, reports, today),
                click=link_for(alert, reports[0].day),
                tags=["zap"] if any(r.cheap for r in reports) else ["warning"],
            )
            delivered += 1
        except Exception:
            logger.exception("Kunne ikke sende prisbesked til telefonen for bruger %s", alert.user_id)
    return delivered


def send_due_alerts(now=None, dry_run=False):
    """Sender de forfaldne beskeder (højst én pr. bruger pr. kørsel) og returnerer antal brugere, der fik besked.

    Lykkes ingen kanal, huskes dagen ikke, så den prøves igen næste gang. Ved dry_run sendes og gemmes intet.
    """
    now = now or timezone.now()
    today = now.astimezone(DK_TZ).date()
    sent = 0
    for alert, reports in due_alerts(now):
        with_content = [r for r in reports if r.has_any]
        if dry_run:
            sent += bool(with_content)
            continue
        if with_content:
            if not deliver(alert, with_content, today):
                continue
            sent += 1
        for report in reports:
            AlertLog.objects.get_or_create(user=alert.user, date=report.day, defaults={"sent": report.has_any})
    return sent


def forget_pending_days(user, today=None):
    """Efter ændrede indstillinger skal i dag og i morgen vurderes igen."""
    today = today or timezone.localdate()
    AlertLog.objects.filter(user=user, date__gte=today).delete()
