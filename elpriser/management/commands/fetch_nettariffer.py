from django.core.management.base import BaseCommand, CommandError

from elpriser import grid_client
from elpriser.client import EnergiDataError
from elpriser.models import GridSettings
from elpriser.service import update_companies, update_tariffs


class Command(BaseCommand):
    help = (
        "Henter netselskabernes tariffer fra Energinet for de netselskaber, brugerne har valgt. "
        "Tariffer ændres sjældent; kør den fx én gang om ugen (cron)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--companies", action="store_true", help="Opdatér også listen over netselskaber.")
        parser.add_argument("--gln", action="append", default=[], help="Hent også dette netselskab (GLN). Kan gentages.")
        parser.add_argument("--check-api", action="store_true", help="Kontrollér forbindelse og datasættets opbygning uden at gemme.")

    def handle(self, *args, companies, gln, check_api, **options):
        if check_api:
            return self.check_api()
        try:
            if companies:
                self.stdout.write(f"Netselskaber opdateret: {update_companies()}")
            chosen = set(
                GridSettings.objects.filter(source=GridSettings.Source.DATAHUB, company__isnull=False).values_list("company__gln", flat=True)
            ) | set(gln)
            if not chosen:
                self.stdout.write("Ingen valgte netselskaber at hente tariffer for. (Brug --gln eller lad en bruger vælge netselskab.)")
                return
            new, updated = update_tariffs(chosen)
        except EnergiDataError as error:
            raise CommandError(f"Hentningen fejlede: {error}")
        self.stdout.write(self.style.SUCCESS(f"Tariffer for {len(chosen)} netselskab(er): {new} nye og {updated} opdaterede."))
        if new + updated == 0:
            self.stdout.write(self.style.WARNING("Ingen tariffer fundet. Kør med --check-api for at undersøge."))

    def check_api(self):
        self.stdout.write(f"Datasæt: {grid_client.dataset()}")
        try:
            companies = grid_client.fetch_companies()
        except EnergiDataError as error:
            raise CommandError(f"Kunne ikke hente netselskaber: {error}")
        if not companies:
            raise CommandError("Svaret indeholder ingen netselskaber.")
        self.stdout.write(self.style.SUCCESS(f"OK: {len(companies)} netselskaber, fx: " + ", ".join(list(companies.values())[:5])))
        gln, name = next(iter(companies.items()))
        try:
            records = grid_client._records(grid_client.build_url({"GLN_Number": [gln]}))
            rows = grid_client.parse_tariffs(records)
        except EnergiDataError as error:
            raise CommandError(f"Kunne ikke hente tariffer for {name}: {error}")
        if records:
            self.stdout.write(f"Kolonner i tarifsvaret: {', '.join(records[0])}")
        if not rows:
            raise CommandError(f"Ingen tariffer kunne tolkes for {name} ({len(records)} rækker i svaret).")
        sample = rows[0]
        self.stdout.write(
            f"Eksempel: {name} · {sample.code} · {sample.name} · gælder {sample.valid_from} – {sample.valid_to or 'uden slutdato'}"
        )
        low, high = min(sample.hour_prices), max(sample.hour_prices)
        self.stdout.write(
            f"Priser pr. time: {low:.4f}–{high:.4f} DKK/kWh = {low * 100:.1f}–{high * 100:.1f} øre/kWh (uden moms). "
            "Passer det med dit netselskabs prisblad?"
        )
        self.stdout.write(self.style.SUCCESS(f"OK: {len(rows)} tariffer kan tolkes."))
