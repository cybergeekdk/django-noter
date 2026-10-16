from django.core.management.base import BaseCommand

from inventory.reminders import send_due_reminders


class Command(BaseCommand):
    help = "Sender e-mailpåmindelser om garantier, der udløber snart. Kør den dagligt (cron)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Vis kun, hvor mange mails der ville blive sendt.")

    def handle(self, *args, dry_run=False, **options):
        count = send_due_reminders(dry_run=dry_run)
        verb = "ville blive sendt" if dry_run else "sendt"
        self.stdout.write(f"{count} påmindelse(r) {verb}.")
