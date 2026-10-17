from datetime import date, timedelta

from django.contrib import messages
from django.core.mail import EmailMessage
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import dateformat, timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET, require_POST

from . import alerts as alert_logic
from . import analysis
from .forms import AlertForm
from .client import VALID_AREAS, EnergiDataError
from .models import CheapPowerAlert, FetchRun, SpotPrice
from .service import DK_TZ, local_today, update_prices

STALE_AFTER = timedelta(hours=26)
MAX_DAYS = 366


class BadParameter(Exception):
    pass


def get_area(request):
    area = request.GET.get("omraade", "DK1").upper()
    if area not in VALID_AREAS:
        raise BadParameter("Ukendt prisområde. Brug DK1 eller DK2.")
    return area


def get_day(request):
    value = request.GET.get("dato")
    if not value:
        return local_today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise BadParameter("Ugyldig dato. Brug formatet ÅÅÅÅ-MM-DD.")


def get_flag(request, name):
    return request.GET.get(name, "").lower() in ("1", "true", "ja")


def get_days(request, default=30):
    value = request.GET.get("dage", str(default))
    if not value.isdigit() or not 1 <= int(value) <= MAX_DAYS:
        raise BadParameter(f"«dage» skal være et tal fra 1 til {MAX_DAYS}.")
    return int(value)


def json_api(view):
    """Fælles for JSON-endpoints: kun GET, korte svar-cache og 400 ved ugyldige parametre."""

    @require_GET
    @cache_control(public=True, max_age=60)
    def wrapper(request):
        try:
            return JsonResponse(view(request))
        except BadParameter as error:
            return JsonResponse({"error": str(error)}, status=400)

    wrapper.__name__ = view.__name__
    return wrapper


def last_success():
    return FetchRun.objects.filter(ok=True).order_by("-finished").first()


@json_api
def api_day(request):
    area, day = get_area(request), get_day(request)
    vat, hourly = get_flag(request, "moms"), request.GET.get("opl") == "time"

    points = analysis.day_points(area, day)
    step = analysis.step_minutes(points)
    if hourly and step < 60:
        points, step = analysis.hourly(points), 60

    def value(price):
        return analysis.to_ore_kwh(price, vat)

    def labelled(moment, price):
        return {"t": moment.astimezone(DK_TZ).isoformat(), "label": analysis.local_label(moment), "v": value(price)}

    summary = analysis.summarize(points)
    now_point = analysis.current_point(points, step, timezone.now())
    window = analysis.cheapest_window(points, step)
    run = last_success()
    return {
        "area": area,
        "date": day.isoformat(),
        "unit": "øre/kWh",
        "vat": vat,
        "step_minutes": step,
        "has_data": bool(points),
        "points": [labelled(m, p) for m, p in points],
        "current": labelled(*now_point) if now_point else None,
        "min": labelled(*summary["min"]) if summary else None,
        "max": labelled(*summary["max"]) if summary else None,
        "avg": value(summary["avg"]) if summary else None,
        "cheapest_window": (
            {
                "hours": 3,
                "from": analysis.local_label(window["start"]),
                "to": analysis.local_label(window["end"]),
                "avg": value(window["avg"]),
            }
            if window
            else None
        ),
        "updated": run.finished.isoformat() if run else None,
    }


@json_api
def api_days(request):
    area, days, vat = get_area(request), get_days(request), get_flag(request, "moms")
    last = local_today()  # perioden slutter i dag og er præcis `days` dage lang
    first = last - timedelta(days=days - 1)
    rows = analysis.daily_averages(area, first, last)
    return {
        "area": area,
        "unit": "øre/kWh",
        "vat": vat,
        "days": [
            {
                "date": day.isoformat(),
                "avg": analysis.to_ore_kwh(avg, vat),
                "min": analysis.to_ore_kwh(low, vat),
                "max": analysis.to_ore_kwh(high, vat),
            }
            for day, avg, low, high in rows
        ],
    }


@json_api
def api_profile(request):
    area, days, vat = get_area(request), get_days(request), get_flag(request, "moms")
    last = local_today()
    first = last - timedelta(days=days - 1)
    rows = analysis.hour_profile(area, first, last)
    return {
        "area": area,
        "unit": "øre/kWh",
        "vat": vat,
        "days": days,
        "hours": [{"hour": hour, "label": f"{hour:02d}", "v": analysis.to_ore_kwh(avg, vat)} for hour, avg in rows],
    }


def index(request):
    alert = CheapPowerAlert.objects.filter(user=request.user).first() if request.user.is_authenticated else None
    run = last_success()
    latest = FetchRun.objects.order_by("-started").first()
    stale = run is None or timezone.now() - run.finished > STALE_AFTER
    context = {
        "areas": SpotPrice.Area.choices,
        "today": local_today().isoformat(),
        "last_success": run,
        "stale": stale,
        "last_failed": latest if latest and not latest.ok else None,
        "alert": alert if alert and alert.enabled else None,
    }
    return render(request, "elpriser/index.html", context)


@login_required
@user_passes_test(lambda user: user.is_superuser)
@require_POST
def refresh(request):
    """Henter priser nu (kun super admin). Praktisk til at teste forbindelsen til Energinet."""
    try:
        result = update_prices(days_back=1, days_ahead=1)
    except EnergiDataError as error:
        messages.error(request, f"Hentningen fejlede: {error}")
    except Exception as error:  # vis fejlen i stedet for en 500-side
        messages.error(request, f"Uventet fejl ved hentning: {type(error).__name__}: {error}")
    else:
        messages.success(request, f"Priser opdateret: {result.rows_new} nye og {result.rows_updated} opdaterede rækker.")
    return redirect("elpriser:index")


# --- Besked når strømmen er billig ------------------------------------------------------------


def alert_preview(alert):
    """Hvornår ville brugeren have fået besked de seneste dage (og i morgen)? Hjælper med at vælge en grænse."""
    today = local_today()
    days = []
    for offset in range(-6, 2):
        day = today + timedelta(days=offset)
        periods, ready = alert_logic.day_periods(alert, day)
        if not ready:
            continue
        label = "I dag" if offset == 0 else "I morgen" if offset == 1 else dateformat.format(day, "D d/m")  # dansk ugedag
        days.append({"date": day, "label": label, "periods": periods})
    first, _ = analysis.day_bounds(today - timedelta(days=29))
    prices = list(SpotPrice.objects.filter(area=alert.area, time__gte=first).values_list("price_dkk_mwh", flat=True))
    share = None
    if prices:
        cheap = sum(1 for p in prices if analysis.to_ore_kwh(p, alert.incl_vat) <= float(alert.threshold_ore))
        share = round(100 * cheap / len(prices))
    return {"days": days, "share": share}


@login_required
def alerts(request):
    alert = CheapPowerAlert.objects.get_or_create(user=request.user)[0]
    if request.method == "POST":
        form = AlertForm(request.POST, instance=alert, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Indstillingerne er gemt.")
            return redirect("elpriser:alerts")
        status = 400
    else:
        form, status = AlertForm(instance=alert, user=request.user), 200
    context = {"form": form, "preview": alert_preview(alert), "alert": alert}
    return render(request, "elpriser/alerts.html", context, status=status)


@login_required
@require_POST
def alerts_test(request):
    """Sender en testmail til brugerens egen adresse, så man kan se, at mailopsætningen virker."""
    if not request.user.email:
        messages.error(request, "Gem først en e-mailadresse.")
    else:
        try:
            EmailMessage(
                "Testmail fra elpriserne",
                "Hvis du kan læse denne mail, virker beskeder om billig strøm.",
                to=[request.user.email],
            ).send()
            messages.success(request, f"Testmail sendt til {request.user.email}.")
        except Exception:
            messages.error(request, "Mailen kunne ikke sendes. Tjek mailopsætningen på serveren.")
    return redirect("elpriser:alerts")
