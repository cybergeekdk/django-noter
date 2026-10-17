from datetime import date, timedelta

from django.contrib import messages
from django.core.mail import EmailMessage
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import dateformat, timezone
from django.utils.cache import patch_vary_headers
from django.views.decorators.http import require_GET, require_POST

from . import alerts as alert_logic
from . import analysis, grid, push
from .forms import AlertForm, GridForm
from .client import VALID_AREAS, EnergiDataError
from .models import CheapPowerAlert, FetchRun, GridCompany, GridSettings, GridTariff, SpotPrice
from .service import DK_TZ, local_today, update_companies, update_prices, update_tariffs

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
    def wrapper(request):
        try:
            response = JsonResponse(view(request))
        except BadParameter as error:
            return JsonResponse({"error": str(error)}, status=400)
        if request.GET.get("nettarif") == "1":
            # Svaret afhænger af brugerens egne indstillinger og må ikke ligge i en fælles cache.
            response["Cache-Control"] = "private, max-age=60"
            patch_vary_headers(response, ["Cookie"])
        else:
            response["Cache-Control"] = "public, max-age=60"
        return response

    wrapper.__name__ = view.__name__
    return wrapper


def last_success():
    return FetchRun.objects.filter(ok=True).order_by("-started").first()


def get_pricing(request):
    """(Pricing eller None, info). Nettarif kræver ?nettarif=1, login og udfyldte indstillinger."""
    info = {"requested": False, "enabled": False, "reason": None}
    if request.GET.get("nettarif") != "1":
        return None, info
    info["requested"] = True
    config = grid.settings_for(request.user)
    if not request.user.is_authenticated:
        info["reason"] = "Log ind for at se prisen inkl. nettarif."
    elif config is None:
        info["reason"] = "Du har ikke valgt netselskab og tarif endnu."
    else:
        info["enabled"] = True
        return grid.Pricing(config), info
    return None, info


@json_api
def api_day(request):
    area, day = get_area(request), get_day(request)
    vat, hourly = get_flag(request, "moms"), request.GET.get("opl") == "time"
    pricing, grid_info = get_pricing(request)
    if pricing and not pricing.has_data(day):
        pricing, grid_info = None, {**grid_info, "enabled": False, "reason": "Der er ingen nettarif for den valgte dag."}

    spot_points = analysis.day_points(area, day)
    step = analysis.step_minutes(spot_points)
    if hourly and step < 60:
        spot_points, step = analysis.hourly(spot_points), 60
    points = grid.apply(spot_points, pricing) if pricing else spot_points
    spot_by_time = dict(spot_points)

    def value(price):
        return analysis.to_ore_kwh(price, vat)

    def labelled(moment, price):
        item = {"t": moment.astimezone(DK_TZ).isoformat(), "label": analysis.local_label(moment), "v": value(price)}
        if pricing:  # opdeling i spotpris og nettarif m.m. til den stablede graf
            item["spot"] = value(spot_by_time[moment])
            item["grid"] = round(pricing.grid_ore(moment) * (analysis.VAT if vat else 1), 2)
        return item

    summary = analysis.summarize(points)
    now_point = analysis.current_point(points, step, timezone.now())
    window = analysis.cheapest_window(points, step)
    run = last_success()
    return {
        "area": area,
        "date": day.isoformat(),
        "unit": "øre/kWh",
        "vat": vat,
        "grid": grid_info,
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
    pricing, grid_info = get_pricing(request)
    last = local_today()  # perioden slutter i dag og er præcis `days` dage lang
    first = last - timedelta(days=days - 1)
    rows = (
        analysis.daily_averages_with_grid(area, first, last, pricing)
        if pricing
        else analysis.daily_averages(area, first, last)
    )
    return {
        "area": area,
        "unit": "øre/kWh",
        "vat": vat,
        "grid": grid_info,
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
    pricing, grid_info = get_pricing(request)
    last = local_today()
    first = last - timedelta(days=days - 1)
    rows = (
        analysis.hour_profile_with_grid(area, first, last, pricing)
        if pricing
        else analysis.hour_profile(area, first, last)
    )
    return {
        "area": area,
        "unit": "øre/kWh",
        "vat": vat,
        "grid": grid_info,
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
        "grid_configured": grid.settings_for(request.user) is not None,
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


# --- Besked når strømmen er billig (eller dyr) -------------------------------------------------


def alert_preview(alert):
    """Hvornår ville brugeren have fået besked de seneste dage (og i morgen)? Hjælper med at vælge grænser."""
    today = local_today()
    days = []
    for offset in range(-6, 2):
        day = today + timedelta(days=offset)
        report = alert_logic.day_report(alert, day)
        if report is None:
            continue
        label = "I dag" if offset == 0 else "I morgen" if offset == 1 else dateformat.format(day, "D d/m")  # dansk ugedag
        days.append({"date": day, "label": label, "cheap": report.cheap, "expensive": report.expensive})
    first, _ = analysis.day_bounds(today - timedelta(days=29))
    prices = [
        analysis.to_ore_kwh(p, alert.incl_vat)
        for p in SpotPrice.objects.filter(area=alert.area, time__gte=first).values_list("price_dkk_mwh", flat=True)
    ]
    share = expensive_share = None
    if prices:
        share = round(100 * sum(1 for p in prices if p <= float(alert.threshold_ore)) / len(prices))
        expensive_share = round(100 * sum(1 for p in prices if p > float(alert.expensive_ore)) / len(prices))
    return {"days": days, "share": share, "expensive_share": expensive_share}


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
    context = {
        "form": form,
        "preview": alert_preview(alert),
        "alert": alert,
        "grid_configured": grid.settings_for(request.user) is not None,
        "ntfy_server": push.server(),
        "ntfy_url": push.topic_url(alert.ntfy_topic) if alert.ntfy_topic else "",
    }
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


@login_required
@require_POST
def alerts_test_phone(request):
    """Sender en testbesked til brugerens telefon (ntfy)."""
    alert = CheapPowerAlert.objects.get_or_create(user=request.user)[0]
    if not alert.ntfy_topic:
        messages.error(request, "Slå «besked til telefonen» til og gem først.")
    else:
        try:
            push.send_ntfy(alert.ntfy_topic, "Test fra elpriserne", "Hvis du kan se denne besked, virker telefonbeskeder.", tags=["white_check_mark"])
            messages.success(request, "Testbesked sendt. Kommer den ikke frem, så tjek, at du abonnerer på det rigtige emne i ntfy-appen.")
        except push.PushError as error:
            messages.error(request, f"Testbeskeden kunne ikke sendes: {error}")
    return redirect("elpriser:alerts")


@login_required
@require_POST
def alerts_new_topic(request):
    """Nyt hemmeligt emne (fx hvis det gamle er kommet til andres kendskab)."""
    alert = CheapPowerAlert.objects.get_or_create(user=request.user)[0]
    alert.ntfy_topic = push.new_topic()
    alert.save(update_fields=["ntfy_topic"])
    messages.success(request, "Du har fået et nyt emne. Abonnér på det i ntfy-appen og fjern det gamle.")
    return redirect("elpriser:alerts")


# --- Nettarif --------------------------------------------------------------------------------------


def grid_preview(config):
    """24 værdier (øre/kWh uden moms) for i dag: nettarif og faste tillæg, til visning på indstillingssiden."""
    pricing = grid.Pricing(config)
    hours = pricing.hour_tariffs(local_today())
    if hours is None:
        return None
    fixed = config.fixed_ore
    return {"tariff": [round(h, 2) for h in hours], "fixed": round(fixed, 2), "total": [round(h + fixed, 2) for h in hours]}


def fetch_tariffs_if_missing(request, company):
    """Henter tariffer for netselskabet med det samme, hvis der ingen er (så valget virker uden at vente på cron)."""
    if company is None or GridTariff.objects.filter(gln=company.gln).exists():
        return
    try:
        new, updated = update_tariffs([company.gln])
    except EnergiDataError as error:
        messages.warning(request, f"Kunne ikke hente tariffer for {company.name}: {error}")
    except Exception as error:
        messages.warning(request, f"Uventet fejl ved hentning af tariffer: {type(error).__name__}: {error}")
    else:
        if new + updated == 0:
            messages.warning(request, f"Energinet har ingen tariffer for {company.name}.")


@login_required
def grid_settings(request):
    config = GridSettings.objects.get_or_create(user=request.user)[0]
    status = 200
    if request.method == "POST":
        company_id = request.POST.get("company", "")
        company = GridCompany.objects.filter(pk=company_id).first() if company_id.isdigit() else None
        if request.POST.get("action") == "reload":
            # Kun skift af netselskab: vis dets tariffer uden at gemme eller validere resten.
            fetch_tariffs_if_missing(request, company)
            form = GridForm(initial={"source": request.POST.get("source") or config.source, "company": company},
                            user=request.user, instance=config, selected_company=company or False)
        else:
            fetch_tariffs_if_missing(request, company)
            form = GridForm(request.POST, user=request.user, instance=config)
            if form.is_valid():
                form.save()
                messages.success(request, "Nettarif-indstillingerne er gemt.")
                return redirect("elpriser:grid")
            status = 400
    else:
        form = GridForm(user=request.user, instance=config)
    config.refresh_from_db()
    context = {
        "form": form,
        "preview": grid_preview(config) if config.configured else None,
        "company_count": GridCompany.objects.count(),
        "config": config,
        "latest_tariff": GridTariff.objects.order_by("-fetched").first(),
    }
    return render(request, "elpriser/grid.html", context, status=status)


@login_required
@user_passes_test(lambda user: user.is_superuser)
@require_POST
def grid_refresh_companies(request):
    """Henter listen over netselskaber nu (kun super admin)."""
    try:
        count = update_companies()
    except EnergiDataError as error:
        messages.error(request, f"Hentningen fejlede: {error}")
    except Exception as error:
        messages.error(request, f"Uventet fejl ved hentning: {type(error).__name__}: {error}")
    else:
        messages.success(request, f"Listen over netselskaber er opdateret ({count} selskaber).")
    return redirect("elpriser:grid")
