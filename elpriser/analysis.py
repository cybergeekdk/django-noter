"""Beregninger på priser. Priser er DKK/MWh (spot, uden moms/afgifter/tariffer) indtil `to_ore_kwh`.

En «dag» er altid en dansk kalenderdag (Europe/Copenhagen), så den kan være 23 eller 25 timer lang
ved sommer-/vintertid. Tider gemmes og regnes i UTC og vises i dansk tid.
"""

from collections import defaultdict
from datetime import UTC, datetime, time, timedelta
from statistics import median

from django.db.models import Avg, Max, Min
from django.db.models.functions import TruncDate

from .models import SpotPrice
from .service import DK_TZ

VAT = 1.25


def to_ore_kwh(dkk_mwh, vat=False):
    """DKK/MWh -> øre/kWh (1 DKK/MWh = 0,1 øre/kWh), evt. med 25 % moms."""
    value = float(dkk_mwh) / 10
    return round(value * VAT if vat else value, 2)


def day_bounds(day):
    """[start, slut) for en dansk kalenderdag som UTC-tidspunkter."""
    start = datetime.combine(day, time.min, tzinfo=DK_TZ).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=DK_TZ).astimezone(UTC)
    return start, end


def local_label(moment):
    return moment.astimezone(DK_TZ).strftime("%H:%M")


def day_points(area, day):
    """[(tid_utc, pris_dkk_mwh)] for en dansk dag, sorteret efter tid."""
    start, end = day_bounds(day)
    rows = SpotPrice.objects.filter(area=area, time__gte=start, time__lt=end).order_by("time")
    return [(row.time, float(row.price_dkk_mwh)) for row in rows]


def step_minutes(points):
    """Tidsopløsningen i minutter, aflæst af data (typisk afstand mellem punkterne). 60, hvis ukendt."""
    gaps = [(b[0] - a[0]).total_seconds() / 60 for a, b in zip(points, points[1:])]
    gaps = [g for g in gaps if g > 0]
    return int(median(gaps)) if gaps else 60


def hourly(points):
    """Gennemsnit pr. hel time. Danske tidszoner ligger på hele timer, så UTC-timen er også en lokal time."""
    buckets = defaultdict(list)
    for moment, price in points:
        buckets[moment.replace(minute=0, second=0, microsecond=0)].append(price)
    return [(hour, sum(values) / len(values)) for hour, values in sorted(buckets.items())]


def cheapest_window(points, step, hours=3):
    """Billigste sammenhængende periode på `hours` timer: {'start','end','avg'} eller None."""
    size = round(hours * 60 / step)
    if size < 1 or len(points) < size:
        return None
    best = None
    for i in range(len(points) - size + 1):
        window = points[i : i + size]
        span = (window[-1][0] - window[0][0]).total_seconds() / 60
        if span != (size - 1) * step:  # hul i data: sammenhængen er brudt
            continue
        avg = sum(p for _, p in window) / size
        if best is None or avg < best["avg"]:
            best = {"start": window[0][0], "end": window[-1][0] + timedelta(minutes=step), "avg": avg}
    return best


def current_point(points, step, now):
    """Det punkt, hvis interval `now` ligger i, ellers None."""
    for moment, price in points:
        if moment <= now < moment + timedelta(minutes=step):
            return moment, price
    return None


def summarize(points):
    """Mindste, største og gennemsnit (som (tid, pris)-par). Tom dict uden data."""
    if not points:
        return {}
    low = min(points, key=lambda p: p[1])
    high = max(points, key=lambda p: p[1])
    return {"min": low, "max": high, "avg": sum(p for _, p in points) / len(points)}


def daily_averages(area, first, last):
    """Gennemsnit, laveste og højeste pr. dansk dag fra `first` til og med `last`."""
    start, _ = day_bounds(first)
    _, end = day_bounds(last)
    rows = (
        SpotPrice.objects.filter(area=area, time__gte=start, time__lt=end)
        .annotate(day=TruncDate("time", tzinfo=DK_TZ))
        .values("day")
        .annotate(avg=Avg("price_dkk_mwh"), low=Min("price_dkk_mwh"), high=Max("price_dkk_mwh"))
        .order_by("day")
    )
    return [(r["day"], float(r["avg"]), float(r["low"]), float(r["high"])) for r in rows]


def hour_profile(area, first, last):
    """Gennemsnitspris for hver time på døgnet (0–23) over perioden: typisk døgnprofil."""
    start, _ = day_bounds(first)
    _, end = day_bounds(last)
    buckets = defaultdict(list)
    for row in SpotPrice.objects.filter(area=area, time__gte=start, time__lt=end).only("time", "price_dkk_mwh"):
        buckets[row.time.astimezone(DK_TZ).hour].append(float(row.price_dkk_mwh))
    return [(hour, sum(v) / len(v)) for hour, v in sorted(buckets.items())]
