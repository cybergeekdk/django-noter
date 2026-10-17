"""Henter priser og gemmer dem i databasen (idempotent: kan køres så ofte, man vil)."""

from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from .client import VALID_AREAS, EnergiDataError, fetch_prices
from .models import FetchRun, SpotPrice

DK_TZ = ZoneInfo("Europe/Copenhagen")
CHUNK_DAYS = 30  # længere perioder hentes i bidder, så svarene holdes små


@dataclass
class UpdateResult:
    rows_new: int = 0
    rows_updated: int = 0

    @property
    def rows(self):
        return self.rows_new + self.rows_updated


def local_today():
    return timezone.now().astimezone(DK_TZ).date()


def save_rows(rows):
    """Gemmer rækker med upsert på (område, tid). Returnerer (nye, opdaterede)."""
    if not rows:
        return 0, 0
    existing = set(
        SpotPrice.objects.filter(
            time__gte=min(r.time for r in rows), time__lte=max(r.time for r in rows)
        ).values_list("area", "time")
    )
    now = timezone.now()
    objects = [
        SpotPrice(area=r.area, time=r.time, price_dkk_mwh=r.dkk_mwh, price_eur_mwh=r.eur_mwh, updated=now)
        for r in rows
    ]
    with transaction.atomic():
        SpotPrice.objects.bulk_create(
            objects,
            update_conflicts=True,
            unique_fields=["area", "time"],
            update_fields=["price_dkk_mwh", "price_eur_mwh", "updated"],
        )
    new = sum(1 for r in rows if (r.area, r.time) not in existing)
    return new, len(rows) - new


def update_prices(areas=VALID_AREAS, days_back=1, days_ahead=1, today=None):
    """Henter priser fra `days_back` dage før i dag til og med `days_ahead` dage efter i dag.

    Der hentes lidt mere end bedt om (en dag til hver side), så tidszoneforskelle mellem API og
    Danmark aldrig giver huller. Resultatet logges som en `FetchRun`.
    """
    areas = tuple(areas)
    unknown = set(areas) - set(VALID_AREAS)
    if unknown:
        raise ValueError(f"Ukendt prisområde: {', '.join(sorted(unknown))}")
    if days_back < 0 or days_ahead < 0:
        raise ValueError("Antal dage skal være 0 eller flere.")

    today = today or local_today()
    first, last = today - timedelta(days=days_back), today + timedelta(days=days_ahead)
    run = FetchRun.objects.create()
    result = UpdateResult()
    try:
        window_start = first
        while window_start <= last:
            window_end = min(window_start + timedelta(days=CHUNK_DAYS - 1), last)
            rows = fetch_prices(window_start - timedelta(days=1), window_end + timedelta(days=2), areas)
            new, updated = save_rows(rows)
            result.rows_new += new
            result.rows_updated += updated
            window_start = window_end + timedelta(days=1)
    except Exception as error:
        run.ok, run.finished = False, timezone.now()
        run.rows_new, run.rows_updated = result.rows_new, result.rows_updated
        run.message = str(error) if isinstance(error, EnergiDataError) else f"{type(error).__name__}: {error}"
        run.save()
        raise
    run.ok, run.finished = True, timezone.now()
    run.rows_new, run.rows_updated = result.rows_new, result.rows_updated
    run.message = "Ingen priser fundet i tidsrummet." if result.rows == 0 else ""
    run.save()
    return result
