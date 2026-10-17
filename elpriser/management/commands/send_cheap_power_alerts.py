from django.core.management.base import BaseCommand

from elpriser.alerts import send_due_alerts


class Command(BaseCommand):
    help = (
        "Sender besked til brugere, når der er billig strøm i dag eller i morgen. "
        "Kør den lige efter fetch_elpriser (eller brug fetch_elpriser --notify). Sender højst én mail pr. bruger pr. dag."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Vis kun, hvor mange mails der ville blive sendt.")

    def handle(self, *args, dry_run=False, **options):
        count = send_due_alerts(dry_run=dry_run)
        self.stdout.write(f"{count} besked(er) {'ville blive sendt' if dry_run else 'sendt'}.")
