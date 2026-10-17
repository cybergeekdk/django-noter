"""Klient til Energinets åbne data (Energi Data Service, https://api.energidataservice.dk).

Bemærk: denne kode er skrevet uden mulighed for at kalde det rigtige API (netværket var spærret),
så den er bygget til at fejle tydeligt frem for at gætte:

* Kolonnenavne slås op i `TIME_COLUMNS` / `DKK_COLUMNS` / `EUR_COLUMNS`, så både det nuværende
  datasæt (`DayAheadPrices`) og det udgåede (`Elspotprices`) kan læses.
* Tidsopløsningen (time/kvarter) antages ikke, men aflæses senere af data.
* Svaret kontrolleres (kolonner, afkortning). Passer det ikke, rejses `EnergiDataError`
  med en forklaring, i stedet for at der gemmes forkerte data.

Kør `python manage.py fetch_elpriser --check-api` for at kontrollere mod det rigtige API.
"""

import json
import logging
import time as time_module
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings

logger = logging.getLogger(__name__)

AREA_COLUMN = "PriceArea"
TIME_COLUMNS = ("TimeUTC", "HourUTC")
DKK_COLUMNS = ("DayAheadPriceDKK", "SpotPriceDKK")
EUR_COLUMNS = ("DayAheadPriceEUR", "SpotPriceEUR")
VALID_AREAS = ("DK1", "DK2")

RETRY_DELAYS = (0, 2, 6)  # sekunder før hvert forsøg
RETRY_STATUSES = {429, 500, 502, 503, 504}


class EnergiDataError(Exception):
    """Noget gik galt ved hentning eller tolkning af priser. Beskeden er tænkt til at blive vist."""


@dataclass(frozen=True)
class PriceRow:
    area: str
    time: datetime  # intervallets start, tidszonebevidst UTC
    dkk_mwh: Decimal
    eur_mwh: Decimal | None


def _first_present(record, candidates):
    for name in candidates:
        if name in record:
            return name
    return None


def _to_decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError):
        raise EnergiDataError(f"Ugyldig talværdi i svaret: {value!r}")


def _parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        raise EnergiDataError(f"Ugyldigt tidspunkt i svaret: {value!r}")
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def parse_records(records):
    """Laver API-rækker om til `PriceRow`. Rækker uden pris (endnu ikke offentliggjort) springes over."""
    rows = []
    for record in records:
        if not isinstance(record, dict):
            raise EnergiDataError("Uventet svar: en række er ikke et objekt.")
        time_col = _first_present(record, TIME_COLUMNS)
        dkk_col = _first_present(record, DKK_COLUMNS)
        if AREA_COLUMN not in record or time_col is None or dkk_col is None:
            raise EnergiDataError(
                "Svaret har ikke de forventede kolonner "
                f"({AREA_COLUMN}, {' / '.join(TIME_COLUMNS)}, {' / '.join(DKK_COLUMNS)}). "
                f"Kolonner i svaret: {', '.join(sorted(record))}. "
                "Er datasættet omdøbt? Tjek ENERGINET_DATASET og kør fetch_elpriser --check-api."
            )
        dkk = _to_decimal(record[dkk_col])
        if dkk is None:
            continue
        area = record[AREA_COLUMN]
        if area not in VALID_AREAS:
            continue  # andre prisområder (fx fra et bredere datasæt) er ikke relevante
        eur_col = _first_present(record, EUR_COLUMNS)
        rows.append(
            PriceRow(
                area=area,
                time=_parse_time(record[time_col]),
                dkk_mwh=dkk,
                eur_mwh=_to_decimal(record[eur_col]) if eur_col else None,
            )
        )
    rows.sort(key=lambda r: (r.time, r.area))
    return rows


def build_url(start, end, areas, dataset=None, limit=0):
    """URL til en forespørgsel. `start`/`end` er datoer (end er eksklusiv)."""
    dataset = dataset or settings.ENERGINET_DATASET
    params = {
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "filter": json.dumps({AREA_COLUMN: list(areas)}, separators=(",", ":")),
        "limit": str(limit),
    }
    base = settings.ENERGINET_API_URL.rstrip("/")
    return f"{base}/dataset/{urllib.parse.quote(dataset)}?{urllib.parse.urlencode(params)}"


def get_json(url):
    """GET med timeout og få forsøg. Rejser EnergiDataError med en læsbar forklaring."""
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "django-noter/1.0 (elpriser)"}
    )
    last_error = "ukendt fejl"
    for attempt, delay in enumerate(RETRY_DELAYS, start=1):
        if delay:
            time_module.sleep(delay)
        try:
            with urllib.request.urlopen(request, timeout=settings.ENERGINET_TIMEOUT) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            snippet = error.read(300).decode("utf-8", "replace").strip()
            last_error = f"HTTP {error.code} fra Energinet. {snippet}".strip()
            if error.code not in RETRY_STATUSES:
                raise EnergiDataError(last_error)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = f"Kunne ikke nå Energinet: {getattr(error, 'reason', error)}"
        else:
            try:
                return json.loads(body)
            except ValueError:
                raise EnergiDataError("Svaret fra Energinet er ikke gyldig JSON.")
        logger.warning("Forsøg %s/%s mod Energinet fejlede: %s", attempt, len(RETRY_DELAYS), last_error)
    raise EnergiDataError(f"{last_error} (opgav efter {len(RETRY_DELAYS)} forsøg)")


def fetch_prices(start, end, areas=VALID_AREAS, dataset=None):
    """Henter og tolker priser for [start, end). Returnerer en liste af `PriceRow`."""
    payload = get_json(build_url(start, end, areas, dataset))
    if not isinstance(payload, dict) or "records" not in payload:
        raise EnergiDataError("Uventet svar fra Energinet: feltet «records» mangler.")
    records = payload["records"]
    total = payload.get("total")
    if isinstance(total, int) and total > len(records):
        raise EnergiDataError(
            f"Svaret blev afkortet ({len(records)} af {total} rækker). Hent et kortere tidsrum ad gangen."
        )
    return parse_records(records)
