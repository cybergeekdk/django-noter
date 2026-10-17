"""Beskeder til telefonen via ntfy (https://ntfy.sh): man installerer ntfy-appen og abonnerer på et emne.

Emnet er tilfældigt og genereres af os (aldrig valgt af brugeren), fordi emnet på en offentlig
ntfy-server fungerer som adgangskode. Serveradressen kommer kun fra indstillingerne, så siden
ikke kan bruges til at sende forespørgsler til vilkårlige (fx interne) adresser.

Bemærk: skrevet uden mulighed for at kalde ntfy (netværket var spærret); kun testet med en
erstattet forbindelse. Brug knappen «Send test» på indstillingssiden til at kontrollere det.
"""

import json
import re
import secrets
import urllib.error
import urllib.request

from django.conf import settings

TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
TIMEOUT = 15


class PushError(Exception):
    """En push kunne ikke sendes. Beskeden er tænkt til at blive vist."""


def new_topic():
    return "elpris-" + secrets.token_urlsafe(16)


def server():
    return settings.NTFY_SERVER.rstrip("/")


def topic_url(topic):
    return f"{server()}/{topic}"


def send_ntfy(topic, title, message, click=None, tags=(), priority=3):
    """Sender én besked. Rejser PushError ved fejl."""
    if not TOPIC_RE.match(topic or ""):
        raise PushError("Ugyldigt ntfy-emne.")
    payload = {"topic": topic, "title": title, "message": message, "priority": priority, "tags": list(tags)}
    if click:
        payload["click"] = click
    headers = {"Content-Type": "application/json", "User-Agent": "django-noter/1.0 (elpriser)"}
    if settings.NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {settings.NTFY_TOKEN}"
    request = urllib.request.Request(server() + "/", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            response.read()
    except urllib.error.HTTPError as error:
        raise PushError(f"ntfy svarede HTTP {error.code}.")
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise PushError(f"Kunne ikke nå ntfy: {getattr(error, 'reason', error)}")
