"""Pris inkl. nettarif: spotpris + netselskabets tarif (for den time på døgnet) + faste tillæg.

Alt regnes i øre/kWh uden moms, indtil moms evt. lægges på til sidst. 1 øre/kWh = 10 DKK/MWh, så
tillæg lægges direkte oven på spotpriserne (DKK/MWh), og alle øvrige beregninger virker uændret.
"""

from datetime import date

from django.db.models import Q

from .models import GridSettings, GridTariff
from .service import DK_TZ


def settings_for(user):
    """Brugerens nettarif-indstillinger, hvis de er udfyldt; ellers None."""
    if not user.is_authenticated:
        return None
    found = GridSettings.objects.filter(user=user).select_related("company").first()
    return found if found and found.configured else None


def tariff_on(gln, code, day):
    """Den tarif, der gælder på en dansk kalenderdag (nyeste start vinder), eller None."""
    return (
        GridTariff.objects.filter(gln=gln, code=code, valid_from__lte=day)
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gt=day))
        .order_by("-valid_from")
        .first()
    )


class Pricing:
    """Tillæg oven på spotprisen for en bestemt brugers indstillinger."""

    def __init__(self, grid_settings):
        self.settings = grid_settings
        self._hours = {}

    def hour_tariffs(self, day: date):
        """24 nettarifpriser (øre/kWh uden moms) for dagen, eller None, hvis tariffen mangler."""
        if day not in self._hours:
            hours = None
            if self.settings.source == GridSettings.Source.MANUAL:
                hours = [float(h) for h in self.settings.manual_hours]
            elif self.settings.company_id:
                tariff = tariff_on(self.settings.company.gln, self.settings.tariff_code, day)
                if tariff and len(tariff.hour_prices) == 24:
                    hours = [float(p) * 100 for p in tariff.hour_prices]  # DKK/kWh -> øre/kWh
            self._hours[day] = hours if hours and len(hours) == 24 else None
        return self._hours[day]

    def has_data(self, day: date):
        return self.hour_tariffs(day) is not None

    def grid_ore(self, moment):
        """Nettarif + faste tillæg (øre/kWh uden moms) i det tidspunkt. Kun gyldigt, hvis `has_data`."""
        local = moment.astimezone(DK_TZ)
        hours = self.hour_tariffs(local.date())
        return (hours[local.hour] if hours else 0.0) + self.settings.fixed_ore


def apply(points, pricing):
    """[(tid, DKK/MWh)] med nettarif og tillæg lagt oven på (stadig DKK/MWh)."""
    return [(moment, price + pricing.grid_ore(moment) * 10) for moment, price in points]
