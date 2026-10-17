"""Klient til netselskabernes tariffer i Energinets åbne data (datasættet `DatahubPricelist`).

Bemærk: skrevet uden mulighed for at kalde det rigtige API (netværket var spærret), efter den
opbygning, jeg kender: én række pr. tarif og gyldighedsperiode med `ChargeOwner`, `GLN_Number`,
`ChargeType` (`D03` = tarif), `ChargeTypeCode`, `Note`, `ValidFrom`, `ValidTo` og `Price1`…`Price24`
(DKK/kWh uden moms, én pris for hver time på døgnet). Passer svaret ikke, rejses `EnergiDataError`
med en forklaring i stedet for at der gemmes gæt. Kør `fetch_nettariffer --check-api` for at kontrollere.
"""

import json
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime

from django.conf import settings

from .client import EnergiDataError, get_json
from .service import DK_TZ

DATASET = "DatahubPricelist"
TARIFF_TYPES = {"d03", "tarif"}  # sammenlignes med små bogstaver
REQUIRED = ("GLN_Number", "ChargeOwner", "ChargeType", "ChargeTypeCode", "ValidFrom")
PRICE_COLUMNS = [f"Price{i}" for i in range(1, 25)]
FAR_FUTURE_YEAR = 9000  # «ingen slutdato» skrives ofte som år 9999


@dataclass(frozen=True)
class TariffRow:
    gln: str
    code: str
    name: str
    valid_from: date
    valid_to: date | None
    hour_prices: tuple  # 24 værdier, DKK/kWh uden moms


def dataset():
    return getattr(settings, "ENERGINET_PRICELIST_DATASET", DATASET)


def build_url(filter_=None, columns=None):
    params = {"limit": "0"}
    if filter_:
        params["filter"] = json.dumps(filter_, separators=(",", ":"))
    if columns:
        params["columns"] = ",".join(columns)
    base = settings.ENERGINET_API_URL.rstrip("/")
    return f"{base}/dataset/{urllib.parse.quote(dataset())}?{urllib.parse.urlencode(params)}"


def _records(url):
    payload = get_json(url)
    if not isinstance(payload, dict) or "records" not in payload:
        raise EnergiDataError("Uventet svar fra Energinet: feltet «records» mangler.")
    records, total = payload["records"], payload.get("total")
    if isinstance(total, int) and total > len(records):
        raise EnergiDataError(f"Svaret blev afkortet ({len(records)} af {total} rækker).")
    return records


def _check_columns(record, required):
    missing = [name for name in required if name not in record]
    if missing:
        raise EnergiDataError(
            f"Svaret mangler kolonnerne {', '.join(missing)}. Kolonner i svaret: {', '.join(sorted(record))}. "
            "Er datasættet ændret? Kør fetch_nettariffer --check-api."
        )


def _local_date(value):
    """Gyldighedstidspunkt -> dansk kalenderdato. Tidspunkter uden zone forstås som dansk tid."""
    if value in (None, ""):
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        raise EnergiDataError(f"Ugyldigt tidspunkt i svaret: {value!r}")
    if moment.tzinfo is not None:
        moment = moment.astimezone(DK_TZ)
    return None if moment.year >= FAR_FUTURE_YEAR else moment.date()


def _is_tariff(record):
    return str(record["ChargeType"]).lower() in TARIFF_TYPES


def parse_companies(records):
    """{gln: navn} for de netselskaber (afgiftsejere) der har tariffer."""
    companies = {}
    for record in records:
        if not isinstance(record, dict):
            raise EnergiDataError("Uventet svar: en række er ikke et objekt.")
        _check_columns(record, ("GLN_Number", "ChargeOwner"))
        if "ChargeType" in record and not _is_tariff(record):
            continue
        gln, name = str(record["GLN_Number"]).strip(), str(record["ChargeOwner"]).strip()
        if gln and name:
            companies.setdefault(gln, name)
    return companies


def _price(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise EnergiDataError(f"Ugyldig pris i svaret: {value!r}")


def parse_tariffs(records):
    """Tariffer (ikke abonnementer/gebyrer) som `TariffRow`. Rækker uden pris springes over."""
    rows = []
    for record in records:
        if not isinstance(record, dict):
            raise EnergiDataError("Uventet svar: en række er ikke et objekt.")
        _check_columns(record, REQUIRED)
        if not _is_tariff(record) or record.get("Price1") is None:
            continue
        first = _price(record["Price1"])
        # Flade tariffer har kun Price1; mangler en time, bruges Price1.
        prices = tuple(first if record.get(col) is None else _price(record[col]) for col in PRICE_COLUMNS)
        valid_from = _local_date(record["ValidFrom"])
        if valid_from is None:
            continue
        name = str(record.get("Note") or record.get("Description") or record["ChargeTypeCode"]).strip()
        rows.append(
            TariffRow(
                gln=str(record["GLN_Number"]).strip(),
                code=str(record["ChargeTypeCode"]).strip(),
                name=name[:300],
                valid_from=valid_from,
                valid_to=_local_date(record.get("ValidTo")),
                hour_prices=prices,
            )
        )
    return rows


def fetch_companies():
    """{gln: navn}. Prøver først kun tariffer; er svaret tomt, prøves uden filter."""
    columns = ["ChargeOwner", "GLN_Number", "ChargeType"]
    records = _records(build_url({"ChargeType": ["D03"]}, columns))
    if not records:
        records = _records(build_url(None, columns))
    return parse_companies(records)


def fetch_tariffs(glns):
    records = _records(build_url({"GLN_Number": list(glns)}))
    return parse_tariffs(records)
