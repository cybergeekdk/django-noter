"""E-mailpåmindelser om garantier, der snart udløber.

Kaldes dagligt af `manage.py send_warranty_reminders`. Hver garanti giver kun én påmindelse
(`Item.reminded_at`), så kommandoen kan køres så ofte man vil uden at sende det samme igen.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.formats import date_format

from .models import Item, ReminderPreference

logger = logging.getLogger(__name__)


def when(days):
    return "i dag" if days == 0 else "i morgen" if days == 1 else f"om {days} dage"


def site_url(path):
    return settings.SITE_URL.rstrip("/") + path


def due_items(today=None):
    """Yield (preference, [items]) for brugere, der skal have en påmindelse nu."""
    today = today or timezone.localdate()
    preferences = (
        ReminderPreference.objects.filter(enabled=True, user__is_active=True)
        .exclude(user__email="")
        .select_related("user")
    )
    for preference in preferences:
        cutoff = today + timedelta(days=preference.days_before)
        items = list(
            Item.objects.filter(
                owner=preference.user,
                warranty_expires_on__gte=today,
                warranty_expires_on__lte=cutoff,
                reminded_at__isnull=True,
            )
            .select_related("location")
            .order_by("warranty_expires_on", "name")
        )
        if items:
            yield preference, items


def build_message(preference, items, today):
    user = preference.user
    with translation.override("da"):
        lines = []
        for item in items:
            details = f"{item.quantity} stk."
            if item.location:
                details += f", {item.location.name}"
            expires = date_format(item.warranty_expires_on, "j. F Y")
            lines.append(f"- {item.name} ({details}): udløber {expires} ({when((item.warranty_expires_on - today).days)})")
    body = (
        f"Hej {user.get_username()},\n\n"
        f"Garantien udløber inden for de næste {preference.days_before} dage på:\n\n"
        + "\n".join(lines)
        + f"\n\nSe din udstyrsliste: {site_url(reverse('inventory:list') + '?garanti=snart')}\n\n"
        "Du får denne mail, fordi du har slået garantipåmindelser til.\n"
        f"Du kan ændre eller slå dem fra her: {site_url(reverse('inventory:reminders'))}\n"
    )
    count = len(items)
    subject = f"Garanti udløber snart: {items[0].name}" if count == 1 else f"Garanti udløber snart på {count} ting"
    return EmailMessage(subject, body, to=[user.email])


def send_due_reminders(today=None, dry_run=False):
    """Sender påmindelserne og returnerer antal mails (ved dry_run: antal, der ville blive sendt)."""
    today = today or timezone.localdate()
    sent = 0
    for preference, items in due_items(today):
        if dry_run:
            sent += 1
            continue
        message = build_message(preference, items, today)
        try:
            message.send()
        except Exception:  # én brugers fejl må ikke stoppe de andre; de prøves igen næste gang
            logger.exception("Kunne ikke sende garantipåmindelse til bruger %s", preference.user_id)
            continue
        Item.objects.filter(pk__in=[item.pk for item in items]).update(reminded_at=today)
        sent += 1
    return sent
