from django.core.management.base import BaseCommand, CommandError

from datetime import timedelta

from elpriser import analysis
from elpriser.alerts import send_due_alerts
from elpriser.client import VALID_AREAS, EnergiDataError, build_url, get_json, parse_records
from elpriser.service import local_today, update_prices



class Command(BaseCommand):
    help = (
        "Henter danske elspotpriser fra Energinet (Energi Data Service) og gemmer dem. "
        "Kan køres så tit, man vil (fx hver time); allerede gemte priser opdateres blot."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days-back", type=int, default=1, help="Dage tilbage fra i dag (standard 1). Brug fx 90 til at hente historik.")
        parser.add_argument("--days-ahead", type=int, default=1, help="Dage frem (standard 1 = i morgen).")
        parser.add_argument("--area", action="append", choices=VALID_AREAS, help="Prisområde (kan gentages). Standard: begge.")
        parser.add_argument(
            "--notify",
            action="store_true",
            help="Send bagefter besked til brugere, der har bedt om besked om billig strøm (samme som send_cheap_power_alerts).",
        )
        parser.add_argument(
            "--check-api",
            action="store_true",
            help="Kontrollér forbindelsen og datasættets opbygning uden at gemme noget.",
        )

    def handle(self, *args, days_back, days_ahead, area, check_api, notify=False, **options):
        if check_api:
            return self.check_api()
        try:
            result = update_prices(areas=area or VALID_AREAS, days_back=days_back, days_ahead=days_ahead)
        except EnergiDataError as error:
            raise CommandError(f"Hentningen fejlede: {error}")
        except ValueError as error:
            raise CommandError(str(error))
        self.stdout.write(self.style.SUCCESS(f"Færdig: {result.rows_new} nye og {result.rows_updated} opdaterede priser."))
        if result.rows == 0:
            self.stdout.write(self.style.WARNING("Ingen priser fundet i tidsrummet. Kør med --check-api for at undersøge."))
        if notify:
            self.stdout.write(f"{send_due_alerts()} besked(er) om billig strøm sendt.")

    def check_api(self):
        today = local_today()
        url = build_url(today - timedelta(days=1), today + timedelta(days=2), VALID_AREAS)
        self.stdout.write(f"Kalder: {url}")
        try:
            payload = get_json(url)
        except EnergiDataError as error:
            raise CommandError(f"Kunne ikke hente: {error}")
        records = payload.get("records") if isinstance(payload, dict) else None
        if not records:
            raise CommandError(f"Svaret indeholder ingen rækker. Svar: {str(payload)[:300]}")
        self.stdout.write(f"Rækker: {len(records)} (total ifølge API: {payload.get('total')})")
        self.stdout.write(f"Kolonner i svaret: {', '.join(records[0])}")
        self.stdout.write(f"Første række: {records[0]}")
        try:
            rows = parse_records(records)
        except EnergiDataError as error:
            raise CommandError(str(error))
        points = [(r.time, float(r.dkk_mwh)) for r in rows if r.area == VALID_AREAS[0]]
        step = analysis.step_minutes(points)
        self.stdout.write(self.style.SUCCESS(f"OK: {len(rows)} priser kan tolkes. Tidsopløsning: {step} minutter."))
        self.stdout.write(f"Første tidspunkt: {rows[0].time:%Y-%m-%d %H:%M} UTC, sidste: {rows[-1].time:%Y-%m-%d %H:%M} UTC")
