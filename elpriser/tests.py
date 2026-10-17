"""Tests af elpriser.

VIGTIGT: Netværkskaldet til Energinet er erstattet (mock) i alle tests, og svarene er bygget efter
den forventede opbygning af datasættet (kolonnerne TimeUTC, PriceArea, DayAheadPriceDKK, …).
Det er IKKE verificeret mod det rigtige API. Kør `manage.py fetch_elpriser --check-api` for det.
"""

import io
import json
import urllib.error
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import analysis, client, service
from .client import EnergiDataError, PriceRow, parse_records
from .models import FetchRun, SpotPrice

User = get_user_model()
DAY = date(2026, 6, 15)  # sommertid (UTC+2): lokal midnat = 22:00 UTC dagen før


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def record(time="2026-06-15T10:00:00", area="DK1", dkk=1000.0, eur=134.0, **extra):
    return {"TimeUTC": time, "TimeDK": "ignoreres", "PriceArea": area, "DayAheadPriceDKK": dkk, "DayAheadPriceEUR": eur, **extra}


def make_day(area="DK1", day=DAY, step=60, price=lambda h: 1000 + 10 * h):
    """Opretter en hel dansk dag: pris = price(timeindeks). 1000 DKK/MWh = 100 øre/kWh."""
    start, end = analysis.day_bounds(day)
    moment, rows = start, []
    while moment < end:
        hour = (moment - start).total_seconds() / 3600
        rows.append(SpotPrice(area=area, time=moment, price_dkk_mwh=Decimal(str(price(int(hour)))), price_eur_mwh=None))
        moment += timedelta(minutes=step)
    SpotPrice.objects.bulk_create(rows)
    return rows


def fake_urlopen(payload, status=200):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    response = mock.MagicMock()
    response.read.return_value = body
    response.__enter__.return_value = response
    return mock.Mock(return_value=response)


def http_error(code, body=b""):
    return urllib.error.HTTPError("https://x", code, "fejl", {}, io.BytesIO(body))


class ParseTests(SimpleTestCase):
    def test_parses_records_sorted_in_utc(self):
        rows = parse_records([record("2026-06-15T11:00:00", dkk=2.5), record("2026-06-15T10:00:00", dkk=1000.123456)])
        self.assertEqual([r.time for r in rows], [utc(2026, 6, 15, 10), utc(2026, 6, 15, 11)])
        self.assertEqual(rows[0].dkk_mwh, Decimal("1000.1235"))
        self.assertEqual(rows[0].eur_mwh, Decimal("134.0000"))
        self.assertEqual(rows[0].area, "DK1")

    def test_timestamp_variants(self):
        rows = parse_records([record("2026-06-15T10:00:00Z"), record("2026-06-15T12:00:00+02:00", area="DK2")])
        self.assertEqual({r.time for r in rows}, {utc(2026, 6, 15, 10)})

    def test_rows_without_price_are_skipped(self):
        rows = parse_records([record(dkk=None), record("2026-06-15T11:00:00", dkk=5)])
        self.assertEqual(len(rows), 1)

    def test_negative_and_zero_prices_are_kept(self):
        rows = parse_records([record(dkk=-12.5), record("2026-06-15T11:00:00", dkk=0)])
        self.assertEqual([r.dkk_mwh for r in rows], [Decimal("-12.5000"), Decimal("0.0000")])

    def test_old_elspotprices_columns_are_understood(self):
        rows = parse_records([{"HourUTC": "2025-09-01T10:00:00", "PriceArea": "DK2", "SpotPriceDKK": 700, "SpotPriceEUR": 94}])
        self.assertEqual((rows[0].area, rows[0].dkk_mwh, rows[0].eur_mwh), ("DK2", Decimal("700.0000"), Decimal("94.0000")))

    def test_other_areas_are_ignored(self):
        self.assertEqual(parse_records([record(area="SE3"), record(area="NO2")]), [])

    def test_missing_eur_is_fine(self):
        rows = parse_records([{"TimeUTC": "2026-06-15T10:00:00", "PriceArea": "DK1", "DayAheadPriceDKK": 5}])
        self.assertIsNone(rows[0].eur_mwh)

    def test_unknown_columns_fail_loudly_and_list_what_was_found(self):
        with self.assertRaises(EnergiDataError) as ctx:
            parse_records([{"Tid": "x", "Omraade": "DK1", "Pris": 1}])
        self.assertIn("Omraade", str(ctx.exception))
        self.assertIn("check-api", str(ctx.exception))

    def test_garbage_values_fail(self):
        for bad in ([record(dkk="abc")], [record(time="i går")], ["ikke et objekt"]):
            with self.assertRaises(EnergiDataError):
                parse_records(bad)


class HttpTests(SimpleTestCase):
    def test_build_url(self):
        url = client.build_url(date(2026, 6, 14), date(2026, 6, 17), ["DK1", "DK2"], dataset="DayAheadPrices")
        self.assertIn("/dataset/DayAheadPrices?", url)
        self.assertIn("start=2026-06-14", url)
        self.assertIn("end=2026-06-17", url)
        self.assertIn("limit=0", url)
        self.assertIn("%22DK1%22%2C%22DK2%22", url)

    def test_fetch_prices_success(self):
        opener = fake_urlopen({"total": 1, "records": [record()]})
        with mock.patch("elpriser.client.urllib.request.urlopen", opener):
            rows = client.fetch_prices(date(2026, 6, 15), date(2026, 6, 16))
        self.assertEqual(len(rows), 1)
        self.assertEqual(opener.call_args.kwargs["timeout"], 30)

    def test_truncated_response_is_an_error(self):
        opener = fake_urlopen({"total": 5000, "records": [record()]})
        with mock.patch("elpriser.client.urllib.request.urlopen", opener), self.assertRaisesMessage(EnergiDataError, "afkortet"):
            client.fetch_prices(date(2026, 6, 15), date(2026, 6, 16))

    def test_missing_records_key_and_bad_json(self):
        for payload in ({"fejl": 1}, [1, 2], b"<html>vedligehold</html>"):
            with mock.patch("elpriser.client.urllib.request.urlopen", fake_urlopen(payload)), self.assertRaises(EnergiDataError):
                client.fetch_prices(date(2026, 6, 15), date(2026, 6, 16))

    def test_retries_server_errors_then_succeeds(self):
        ok = fake_urlopen({"total": 1, "records": [record()]})
        opener = mock.Mock(side_effect=[http_error(503), http_error(500), ok.return_value])
        with mock.patch("elpriser.client.urllib.request.urlopen", opener), mock.patch("elpriser.client.time_module.sleep") as sleep:
            rows = client.fetch_prices(date(2026, 6, 15), date(2026, 6, 16))
        self.assertEqual((len(rows), opener.call_count), (1, 3))
        self.assertEqual(sleep.call_count, 2)

    def test_gives_up_after_three_attempts(self):
        opener = mock.Mock(side_effect=http_error(503, b"nede"))
        with mock.patch("elpriser.client.urllib.request.urlopen", opener), mock.patch("elpriser.client.time_module.sleep"):
            with self.assertRaisesMessage(EnergiDataError, "opgav efter 3"):
                client.fetch_prices(date(2026, 6, 15), date(2026, 6, 16))
        self.assertEqual(opener.call_count, 3)

    def test_client_errors_are_not_retried(self):
        opener = mock.Mock(side_effect=http_error(400, b'{"message":"Ugyldigt filter"}'))
        with mock.patch("elpriser.client.urllib.request.urlopen", opener), mock.patch("elpriser.client.time_module.sleep"):
            with self.assertRaisesMessage(EnergiDataError, "Ugyldigt filter"):
                client.fetch_prices(date(2026, 6, 15), date(2026, 6, 16))
        self.assertEqual(opener.call_count, 1)

    def test_network_and_timeout_errors(self):
        for error in (urllib.error.URLError("ingen forbindelse"), TimeoutError("langsom")):
            opener = mock.Mock(side_effect=error)
            with mock.patch("elpriser.client.urllib.request.urlopen", opener), mock.patch("elpriser.client.time_module.sleep"):
                with self.assertRaises(EnergiDataError):
                    client.fetch_prices(date(2026, 6, 15), date(2026, 6, 16))
            self.assertEqual(opener.call_count, 3)


class ServiceTests(TestCase):
    def rows(self, *prices, area="DK1"):
        return [PriceRow(area, utc(2026, 6, 15, 8 + i), Decimal(p), None) for i, p in enumerate(prices)]

    def test_save_rows_is_idempotent_and_updates_changed_prices(self):
        self.assertEqual(service.save_rows(self.rows("100", "200")), (2, 0))
        self.assertEqual(service.save_rows(self.rows("100", "200")), (0, 2))
        self.assertEqual(SpotPrice.objects.count(), 2)
        self.assertEqual(service.save_rows(self.rows("100", "250", "300")), (1, 2))
        self.assertEqual(SpotPrice.objects.get(time=utc(2026, 6, 15, 9)).price_dkk_mwh, Decimal("250"))

    def test_same_time_in_two_areas_are_separate_rows(self):
        service.save_rows(self.rows("100") + self.rows("300", area="DK2"))
        self.assertEqual(SpotPrice.objects.count(), 2)

    def test_update_prices_requests_padded_window_and_logs_success(self):
        with mock.patch("elpriser.service.fetch_prices", return_value=self.rows("100", "200")) as fetch:
            result = service.update_prices(days_back=1, days_ahead=1, today=DAY)
        (start, end, areas), _ = fetch.call_args
        self.assertEqual((start, end), (date(2026, 6, 13), date(2026, 6, 18)))  # 14.–16. juni + én dag til hver side
        self.assertEqual(areas, ("DK1", "DK2"))
        self.assertEqual((result.rows_new, result.rows_updated), (2, 0))
        run = FetchRun.objects.get()
        self.assertTrue(run.ok)
        self.assertIsNotNone(run.finished)
        self.assertEqual((run.rows_new, run.message), (2, ""))

    def test_empty_result_is_ok_but_noted(self):
        with mock.patch("elpriser.service.fetch_prices", return_value=[]):
            service.update_prices(today=DAY)
        self.assertIn("Ingen priser", FetchRun.objects.get().message)

    def test_failure_is_logged_and_reraised(self):
        with mock.patch("elpriser.service.fetch_prices", side_effect=EnergiDataError("Energinet er nede")):
            with self.assertRaises(EnergiDataError):
                service.update_prices(today=DAY)
        run = FetchRun.objects.get()
        self.assertFalse(run.ok)
        self.assertEqual(run.message, "Energinet er nede")

    def test_unexpected_errors_are_logged_too(self):
        with mock.patch("elpriser.service.fetch_prices", side_effect=KeyError("x")):
            with self.assertRaises(KeyError):
                service.update_prices(today=DAY)
        self.assertIn("KeyError", FetchRun.objects.get().message)

    def test_long_backfill_is_chunked(self):
        with mock.patch("elpriser.service.fetch_prices", return_value=[]) as fetch:
            service.update_prices(days_back=64, days_ahead=1, today=DAY)
        self.assertEqual(fetch.call_count, 3)  # 66 dage i bidder à højst 30
        windows = [(c.args[0], c.args[1]) for c in fetch.call_args_list]
        for (_, prev_end), (next_start, _) in zip(windows, windows[1:]):
            self.assertLessEqual(next_start, prev_end)  # ingen huller mellem bidderne

    def test_rejects_bad_arguments_before_calling_api(self):
        with mock.patch("elpriser.service.fetch_prices") as fetch:
            for kwargs in ({"areas": ["SE3"]}, {"days_back": -1}, {"days_ahead": -1}):
                with self.assertRaises(ValueError):
                    service.update_prices(today=DAY, **kwargs)
        fetch.assert_not_called()
        self.assertFalse(FetchRun.objects.exists())


class AnalysisTests(TestCase):
    def test_ore_conversion(self):
        self.assertEqual(analysis.to_ore_kwh(1000), 100.0)
        self.assertEqual(analysis.to_ore_kwh(1000, vat=True), 125.0)
        self.assertEqual(analysis.to_ore_kwh(-50), -5.0)
        self.assertEqual(analysis.to_ore_kwh(Decimal("123.4567")), 12.35)

    def test_day_bounds_follow_danish_time_including_dst(self):
        start, end = analysis.day_bounds(DAY)
        self.assertEqual((start, end), (utc(2026, 6, 14, 22), utc(2026, 6, 15, 22)))
        winter = analysis.day_bounds(date(2026, 1, 15))
        self.assertEqual(winter[0], utc(2026, 1, 14, 23))
        hours = lambda d: (analysis.day_bounds(d)[1] - analysis.day_bounds(d)[0]).total_seconds() / 3600
        self.assertEqual(hours(date(2026, 3, 29)), 23)  # forårs-omstilling
        self.assertEqual(hours(date(2026, 10, 25)), 25)  # efterårs-omstilling
        self.assertEqual(hours(DAY), 24)

    def test_step_is_inferred_from_data(self):
        make_day(step=15)
        self.assertEqual(analysis.step_minutes(analysis.day_points("DK1", DAY)), 15)
        self.assertEqual(analysis.step_minutes([]), 60)
        self.assertEqual(analysis.step_minutes([(utc(2026, 1, 1), 1.0)]), 60)

    def test_hourly_aggregation(self):
        points = [(utc(2026, 6, 15, 8, m), p) for m, p in ((0, 10.0), (15, 20.0), (30, 30.0), (45, 40.0))]
        points.append((utc(2026, 6, 15, 9, 0), 100.0))
        self.assertEqual(analysis.hourly(points), [(utc(2026, 6, 15, 8), 25.0), (utc(2026, 6, 15, 9), 100.0)])

    def test_cheapest_window(self):
        prices = [50, 40, 10, 20, 30, 90, 5, 100]
        points = [(utc(2026, 6, 15, h), float(p)) for h, p in enumerate(prices)]
        window = analysis.cheapest_window(points, 60, hours=3)
        self.assertEqual((window["start"], window["end"], window["avg"]), (utc(2026, 6, 15, 2), utc(2026, 6, 15, 5), 20.0))

    def test_cheapest_window_with_quarters_and_edge_cases(self):
        points = [(utc(2026, 6, 15, 0) + timedelta(minutes=15 * i), 100.0 - i) for i in range(16)]  # falder hele tiden
        window = analysis.cheapest_window(points, 15, hours=3)
        self.assertEqual(window["end"], utc(2026, 6, 15, 4))
        self.assertIsNone(analysis.cheapest_window(points[:5], 15, hours=3))
        self.assertIsNone(analysis.cheapest_window([], 60))

    def test_cheapest_window_skips_windows_with_gaps(self):
        points = [(utc(2026, 6, 15, h), p) for h, p in ((0, 100.0), (1, 100.0), (2, 100.0), (5, 1.0), (6, 1.0))]
        window = analysis.cheapest_window(points, 60, hours=3)
        self.assertEqual(window["avg"], 100.0)  # (5,6) alene er kun to timer; hullet må ikke give et falsk «billigt» vindue

    def test_current_point(self):
        points = [(utc(2026, 6, 15, 8, 0), 1.0), (utc(2026, 6, 15, 8, 15), 2.0)]
        self.assertEqual(analysis.current_point(points, 15, utc(2026, 6, 15, 8, 14, 59)), points[0])
        self.assertEqual(analysis.current_point(points, 15, utc(2026, 6, 15, 8, 15)), points[1])
        self.assertIsNone(analysis.current_point(points, 15, utc(2026, 6, 15, 8, 30)))

    def test_summarize(self):
        points = [(utc(2026, 6, 15, 8), 30.0), (utc(2026, 6, 15, 9), -10.0), (utc(2026, 6, 15, 10), 40.0)]
        summary = analysis.summarize(points)
        self.assertEqual((summary["min"][1], summary["max"][1], summary["avg"]), (-10.0, 40.0, 20.0))
        self.assertEqual(analysis.summarize([]), {})

    def test_daily_averages_group_by_danish_date(self):
        # 22:30 UTC den 14. juni er 00:30 dansk tid den 15. juni
        SpotPrice.objects.create(area="DK1", time=utc(2026, 6, 14, 21, 30), price_dkk_mwh=100)  # 23:30 den 14.
        SpotPrice.objects.create(area="DK1", time=utc(2026, 6, 14, 22, 30), price_dkk_mwh=200)  # 00:30 den 15.
        SpotPrice.objects.create(area="DK1", time=utc(2026, 6, 15, 10, 0), price_dkk_mwh=400)
        SpotPrice.objects.create(area="DK2", time=utc(2026, 6, 15, 10, 0), price_dkk_mwh=9999)  # andet område
        rows = analysis.daily_averages("DK1", date(2026, 6, 14), date(2026, 6, 15))
        self.assertEqual(rows, [(date(2026, 6, 14), 100.0, 100.0, 100.0), (date(2026, 6, 15), 300.0, 200.0, 400.0)])

    def test_hour_profile_uses_danish_hours(self):
        make_day(price=lambda h: 1000 + 10 * h)
        make_day(day=DAY + timedelta(days=1), price=lambda h: 2000 + 10 * h)
        profile = dict(analysis.hour_profile("DK1", DAY, DAY + timedelta(days=1)))
        self.assertEqual(len(profile), 24)
        self.assertEqual(profile[0], 1500.0)  # dansk midnat: snit af 1000 og 2000
        self.assertEqual(profile[23], 1730.0)  # kl. 23: snit af 1230 og 2230


@mock.patch("elpriser.views.local_today", return_value=DAY)
class ApiTests(TestCase):
    def get(self, name, **params):
        return self.client.get(reverse(f"elpriser:{name}"), params)

    def test_day_values_units_and_vat(self, _):
        make_day()
        data = self.get("api_day", omraade="DK1", dato="2026-06-15").json()
        self.assertEqual((data["area"], data["date"], data["unit"], data["vat"]), ("DK1", "2026-06-15", "øre/kWh", False))
        self.assertTrue(data["has_data"])
        self.assertEqual(len(data["points"]), 24)
        self.assertEqual(data["points"][0]["label"], "00:00")
        self.assertEqual(data["points"][0]["v"], 100.0)
        self.assertEqual((data["min"]["label"], data["max"]["label"]), ("00:00", "23:00"))
        self.assertEqual(data["avg"], 111.5)
        self.assertEqual(data["cheapest_window"], {"hours": 3, "from": "00:00", "to": "03:00", "avg": 101.0})
        with_vat = self.get("api_day", omraade="DK1", dato="2026-06-15", moms="1").json()
        self.assertEqual((with_vat["vat"], with_vat["points"][0]["v"]), (True, 125.0))

    def test_current_price_only_when_now_is_inside_the_day(self, _):
        make_day()
        with mock.patch("django.utils.timezone.now", return_value=utc(2026, 6, 15, 8, 20)):  # 10:20 dansk tid
            data = self.get("api_day", dato="2026-06-15").json()
            other_day = self.get("api_day", dato="2026-06-14").json()
        self.assertEqual((data["current"]["label"], data["current"]["v"]), ("10:00", 110.0))
        self.assertIsNone(other_day["current"])

    def test_quarter_hour_data_native_and_hourly(self, _):
        make_day(step=15)
        native = self.get("api_day", dato="2026-06-15").json()
        self.assertEqual((len(native["points"]), native["step_minutes"]), (96, 15))
        hourly = self.get("api_day", dato="2026-06-15", opl="time").json()
        self.assertEqual((len(hourly["points"]), hourly["step_minutes"]), (24, 60))
        self.assertEqual(hourly["points"][5]["label"], "05:00")
        self.assertEqual(native["cheapest_window"]["to"], "03:00")

    def test_hourly_option_leaves_hourly_data_alone(self, _):
        make_day(step=60)
        self.assertEqual(len(self.get("api_day", dato="2026-06-15", opl="time").json()["points"]), 24)

    def test_areas_are_separate(self, _):
        make_day("DK1")
        make_day("DK2", price=lambda h: 5000)
        self.assertEqual(self.get("api_day", omraade="DK2", dato="2026-06-15").json()["points"][0]["v"], 500.0)
        self.assertEqual(self.get("api_day", omraade="dk1", dato="2026-06-15").json()["area"], "DK1")

    def test_day_without_data(self, _):
        data = self.get("api_day", dato="2030-01-01").json()
        self.assertFalse(data["has_data"])
        self.assertEqual((data["points"], data["min"], data["avg"], data["cheapest_window"], data["current"]), ([], None, None, None, None))

    def test_negative_prices(self, _):
        make_day(price=lambda h: -200 if h == 3 else 500)
        data = self.get("api_day", dato="2026-06-15").json()
        self.assertEqual((data["min"]["label"], data["min"]["v"]), ("03:00", -20.0))

    def test_updated_timestamp_comes_from_last_successful_run(self, _):
        self.assertIsNone(self.get("api_day").json()["updated"])
        FetchRun.objects.create(ok=False, finished=utc(2026, 6, 15, 9))
        FetchRun.objects.create(ok=True, finished=utc(2026, 6, 15, 8))
        self.assertTrue(self.get("api_day").json()["updated"].startswith("2026-06-15T08:00"))

    def test_invalid_parameters_give_400(self, _):
        for name, params in [
            ("api_day", {"omraade": "SE3"}), ("api_day", {"dato": "i går"}), ("api_day", {"dato": "2026-13-45"}),
            ("api_days", {"dage": "0"}), ("api_days", {"dage": "9999"}), ("api_days", {"dage": "abc"}),
            ("api_profile", {"dage": "-5"}), ("api_profile", {"omraade": "DK3"}),
        ]:
            response = self.get(name, **params)
            self.assertEqual(response.status_code, 400, (name, params))
            self.assertIn("error", response.json())

    def test_only_get_and_public_with_short_cache(self, _):
        self.assertEqual(self.client.post(reverse("elpriser:api_day")).status_code, 405)
        response = self.get("api_day")
        self.assertEqual(response.status_code, 200)  # ingen login nødvendig: offentlige data
        self.assertIn("max-age=60", response["Cache-Control"])

    def test_daily_endpoint(self, _):
        make_day(day=DAY - timedelta(days=1), price=lambda h: 1000)
        make_day(day=DAY, price=lambda h: 2000)
        make_day("DK2", day=DAY, price=lambda h: 9000)
        data = self.get("api_days", omraade="DK1", dage="7").json()
        self.assertEqual([(d["date"], d["avg"], d["min"], d["max"]) for d in data["days"]],
                         [("2026-06-14", 100.0, 100.0, 100.0), ("2026-06-15", 200.0, 200.0, 200.0)])
        self.assertEqual(self.get("api_days", dage="7", moms="1").json()["days"][0]["avg"], 125.0)

    def test_daily_endpoint_respects_period(self, _):
        make_day(day=DAY - timedelta(days=20))
        make_day(day=DAY)
        self.assertEqual(len(self.get("api_days", dage="7").json()["days"]), 1)
        self.assertEqual(len(self.get("api_days", dage="30").json()["days"]), 2)

    def test_period_is_exactly_the_requested_number_of_days_ending_today(self, _):
        for offset in range(-12, 2):  # data fra 12 dage siden til og med i morgen
            make_day(day=DAY + timedelta(days=offset))
        for days in (1, 7, 10):
            dates = [d["date"] for d in self.get("api_days", dage=str(days)).json()["days"]]
            self.assertEqual(len(dates), days)
            self.assertEqual(dates[-1], "2026-06-15")  # i dag; i morgen er ikke med

    def test_profile_endpoint(self, _):
        make_day(price=lambda h: 1000 + 100 * h)
        hours = self.get("api_profile", dage="7").json()["hours"]
        self.assertEqual(len(hours), 24)
        self.assertEqual((hours[0]["label"], hours[0]["v"], hours[23]["label"], hours[23]["v"]), ("00", 100.0, "23", 330.0))
        self.assertEqual(self.get("api_profile", dage="7").json()["days"], 7)

    def test_empty_database_endpoints(self, _):
        self.assertEqual(self.get("api_days").json()["days"], [])
        self.assertEqual(self.get("api_profile").json()["hours"], [])


@mock.patch("elpriser.views.local_today", return_value=DAY)
class PageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna", password="pw")
        cls.boss = User.objects.create_superuser("boss", password="pw")

    def test_page_is_public_and_in_menu(self, _):
        response = self.client.get(reverse("elpriser:index"))
        self.assertContains(response, "Elpriser")
        self.assertContains(response, "chart.umd.min.js")
        self.assertContains(response, 'data-today="2026-06-15"')
        self.assertContains(response, "uden</strong> nettarif")  # ærlig om, hvad prisen er
        self.assertContains(self.client.get(reverse("pages:home")), reverse("elpriser:index"))

    def test_shows_when_never_updated_and_when_stale(self, _):
        self.assertContains(self.client.get(reverse("elpriser:index")), "ingen priser i databasen")
        FetchRun.objects.create(ok=True, finished=timezone_now() - timedelta(hours=30))
        self.assertContains(self.client.get(reverse("elpriser:index")), "kan være forældede")
        FetchRun.objects.create(ok=True, finished=timezone_now() - timedelta(hours=1))
        response = self.client.get(reverse("elpriser:index"))
        self.assertNotContains(response, "kan være forældede")
        self.assertContains(response, "1\u00a0time siden")

    def test_failure_message_and_refresh_button_only_for_superadmin(self, _):
        FetchRun.objects.create(ok=False, message="Energinet nægtede adgang")
        self.assertNotContains(self.client.get(reverse("elpriser:index")), "nægtede adgang")
        self.assertNotContains(self.client.get(reverse("elpriser:index")), "Opdatér nu")
        self.client.force_login(self.anna)
        self.assertNotContains(self.client.get(reverse("elpriser:index")), "nægtede adgang")
        self.client.force_login(self.boss)
        page = self.client.get(reverse("elpriser:index"))
        self.assertContains(page, "nægtede adgang")
        self.assertContains(page, "Opdatér nu")

    def test_refresh_permissions(self, _):
        url = reverse("elpriser:refresh")
        with mock.patch("elpriser.views.update_prices") as update:
            self.assertEqual(self.client.post(url).status_code, 302)  # anonym -> login
            self.client.force_login(self.anna)
            self.assertEqual(self.client.post(url).status_code, 302)  # almindelig bruger afvises
            self.client.force_login(self.boss)
            self.assertEqual(self.client.get(url).status_code, 405)
            update.assert_not_called()

    def test_refresh_reports_success_and_failure(self, _):
        self.client.force_login(self.boss)
        url = reverse("elpriser:refresh")
        with mock.patch("elpriser.views.update_prices", return_value=service.UpdateResult(3, 4)):
            self.assertContains(self.client.post(url, follow=True), "3 nye og 4 opdaterede")
        with mock.patch("elpriser.views.update_prices", side_effect=EnergiDataError("Energinet er nede")):
            self.assertContains(self.client.post(url, follow=True), "Hentningen fejlede: Energinet er nede")
        with mock.patch("elpriser.views.update_prices", side_effect=RuntimeError("bug")):
            self.assertContains(self.client.post(url, follow=True), "Uventet fejl")


def timezone_now():
    from django.utils import timezone

    return timezone.now()


class CommandTests(TestCase):
    def run_command(self, *args, **kwargs):
        out = io.StringIO()
        call_command("fetch_elpriser", *args, stdout=out, **kwargs)
        return out.getvalue()

    def test_default_run_fetches_and_reports(self):
        with mock.patch("elpriser.management.commands.fetch_elpriser.update_prices", return_value=service.UpdateResult(5, 2)) as update:
            output = self.run_command()
        update.assert_called_once_with(areas=("DK1", "DK2"), days_back=1, days_ahead=1)
        self.assertIn("5 nye og 2 opdaterede", output)

    def test_options_are_passed_through(self):
        with mock.patch("elpriser.management.commands.fetch_elpriser.update_prices", return_value=service.UpdateResult(1, 0)) as update:
            self.run_command("--days-back", "90", "--days-ahead", "0", "--area", "DK2")
        update.assert_called_once_with(areas=["DK2"], days_back=90, days_ahead=0)

    def test_failure_gives_nonzero_exit_via_commanderror(self):
        with mock.patch("elpriser.management.commands.fetch_elpriser.update_prices", side_effect=EnergiDataError("nede")):
            with self.assertRaisesMessage(CommandError, "Hentningen fejlede: nede"):
                self.run_command()

    def test_warns_when_nothing_found(self):
        with mock.patch("elpriser.management.commands.fetch_elpriser.update_prices", return_value=service.UpdateResult(0, 0)):
            self.assertIn("Ingen priser fundet", self.run_command())

    def test_end_to_end_with_mocked_http(self):
        payload = {"total": 2, "records": [record("2026-06-15T10:00:00", dkk=800), record("2026-06-15T10:00:00", area="DK2", dkk=900)]}
        with mock.patch("elpriser.client.urllib.request.urlopen", fake_urlopen(payload)):
            output = self.run_command()
        self.assertIn("2 nye", output)
        self.assertEqual(SpotPrice.objects.count(), 2)
        self.assertTrue(FetchRun.objects.get().ok)

    def test_check_api_reports_columns_and_resolution(self):
        records = [record(f"2026-06-15T10:{m:02d}:00", dkk=800 + m) for m in (0, 15, 30, 45)]
        with mock.patch("elpriser.client.urllib.request.urlopen", fake_urlopen({"total": 4, "records": records})):
            output = self.run_command("--check-api")
        self.assertIn("Kolonner i svaret: TimeUTC", output)
        self.assertIn("Tidsopløsning: 15 minutter", output)
        self.assertFalse(SpotPrice.objects.exists())  # gemmer intet

    def test_check_api_failures_are_explained(self):
        with mock.patch("elpriser.client.urllib.request.urlopen", fake_urlopen({"total": 0, "records": []})):
            with self.assertRaisesMessage(CommandError, "ingen rækker"):
                self.run_command("--check-api")
        with mock.patch("elpriser.client.urllib.request.urlopen", fake_urlopen({"total": 1, "records": [{"a": 1}]})):
            with self.assertRaisesMessage(CommandError, "forventede kolonner"):
                self.run_command("--check-api")
        with mock.patch("elpriser.client.urllib.request.urlopen", mock.Mock(side_effect=http_error(403, b"forbudt"))):
            with self.assertRaisesMessage(CommandError, "HTTP 403"):
                self.run_command("--check-api")


# --- Besked om billig strøm --------------------------------------------------------------------

from . import alerts  # noqa: E402
from .models import AlertLog, CheapPowerAlert  # noqa: E402

NOW = utc(2026, 6, 15, 8, 0)  # 10:00 dansk tid den 15. juni 2026


def cheap_pattern(hour):
    """DKK/MWh pr. dansk time: 0–5 → 300 (30 øre), 6–11 → 800, 12–14 → 200 (20 øre), 15–23 → 900."""
    return 300 if hour < 6 else 800 if hour < 12 else 200 if hour < 15 else 900


def hours_of(period_list):
    return [(p.start.astimezone(service.DK_TZ).strftime("%H:%M"), p.end.astimezone(service.DK_TZ).strftime("%H:%M")) for p in period_list]


class FindPeriodsTests(SimpleTestCase):
    def points(self, prices, step=60):
        return [(utc(2026, 6, 15, 0) + timedelta(minutes=step * i), float(p)) for i, p in enumerate(prices)]

    def find(self, prices, threshold=40, vat=False, minutes=60, step=60):
        return alerts.find_periods(self.points(prices, step), step, threshold, vat, minutes)

    def test_finds_separate_periods_with_stats(self):
        periods = self.find([300, 300, 900, 200, 400, 900])  # 30, 30, 90, 20, 40, 90 øre
        self.assertEqual([(p.start.hour, p.end.hour) for p in periods], [(0, 2), (3, 5)])
        self.assertEqual((periods[0].avg, periods[0].low, periods[0].hours), (30.0, 30.0, 2.0))
        self.assertEqual((periods[1].avg, periods[1].low), (30.0, 20.0))

    def test_threshold_is_inclusive(self):
        self.assertEqual(len(self.find([400])), 1)  # præcis 40 øre
        self.assertEqual(len(self.find([401])), 0)

    def test_vat_changes_what_is_cheap(self):
        self.assertEqual(len(self.find([350], threshold=40, vat=False)), 1)  # 35
        self.assertEqual(len(self.find([350], threshold=40, vat=True)), 0)  # 43,75 med moms

    def test_negative_prices_and_negative_threshold(self):
        self.assertEqual(len(self.find([-50, 300], threshold=0)), 1)  # -5 øre er under 0
        self.assertEqual(len(self.find([-200, 300], threshold=-10)), 1)  # -20 øre er under -10
        self.assertEqual(len(self.find([-50, 300], threshold=-10)), 0)  # -5 øre er IKKE under -10

    def test_minimum_duration(self):
        self.assertEqual(self.find([300, 900, 300, 300, 900], minutes=120)[0].hours, 2.0)
        self.assertEqual(len(self.find([300, 900, 300, 300, 900], minutes=120)), 1)
        self.assertEqual(self.find([300, 900, 300, 900], minutes=120), [])

    def test_quarter_hours(self):
        periods = self.find([300, 300, 300, 300, 900, 300, 300], step=15, minutes=60)
        self.assertEqual(len(periods), 1)
        self.assertEqual(periods[0].hours, 1.0)

    def test_gap_in_data_breaks_a_period(self):
        points = [(utc(2026, 6, 15, 0), 300.0), (utc(2026, 6, 15, 1), 300.0), (utc(2026, 6, 15, 5), 300.0), (utc(2026, 6, 15, 6), 300.0)]
        periods = alerts.find_periods(points, 60, 40, False, 60)
        self.assertEqual([(p.start.hour, p.end.hour) for p in periods], [(0, 2), (5, 7)])

    def test_nothing_cheap(self):
        self.assertEqual(self.find([900, 900]), [])
        self.assertEqual(self.find([]), [])


class AlertSendingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna", password="pw", email="anna@example.dk")
        cls.bo = User.objects.create_user("bo", password="pw", email="bo@example.dk")

    def setUp(self):
        mail.outbox.clear()
        self.alert = CheapPowerAlert.objects.create(user=self.anna, enabled=True, threshold_ore=40, incl_vat=True, min_hours=1)
        self.tomorrow = DAY + timedelta(days=1)

    def send(self, **kwargs):
        return alerts.send_due_alerts(now=NOW, **kwargs)

    def test_sends_one_mail_for_tomorrow_when_prices_are_out(self):
        make_day(day=self.tomorrow, price=cheap_pattern)
        self.assertEqual(self.send(), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["anna@example.dk"])
        self.assertIn("i morgen", message.subject)
        self.assertIn("(DK1)", message.subject)
        self.assertIn("2 perioder", message.subject)
        self.assertIn("kl. 00:00–06:00 (6 timer): gennemsnit 37,5 øre/kWh", message.body)  # 300 DKK/MWh = 30 øre + moms
        self.assertIn("kl. 12:00–15:00 (3 timer): gennemsnit 25,0 øre/kWh, laveste 25,0\n\nSe hele dagen:", message.body)
        self.assertIn("inkl. moms", message.body)
        self.assertIn("dato=2026-06-16", message.body)
        self.assertIn("/elpriser/besked/", message.body)
        self.assertTrue(AlertLog.objects.get(user=self.anna, date=self.tomorrow).sent)

    def test_single_period_subject_has_time_and_price(self):
        make_day(day=self.tomorrow, price=lambda h: 200 if 12 <= h < 15 else 900)
        self.send()
        self.assertEqual(mail.outbox[0].subject, "Billig strøm i morgen (DK1): kl. 12:00–15:00 · 25,0 øre/kWh")

    def test_only_one_mail_per_day_even_when_run_repeatedly(self):
        make_day(day=self.tomorrow, price=cheap_pattern)
        self.assertEqual(self.send(), 1)
        self.assertEqual(self.send(), 0)
        self.assertEqual(self.send(), 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_today_and_tomorrow_are_combined_in_one_mail_and_past_periods_are_dropped(self):
        make_day(day=DAY, price=cheap_pattern)
        make_day(day=self.tomorrow, price=cheap_pattern)
        self.assertEqual(self.send(), 1)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("i dag og i morgen", mail.outbox[0].subject)
        today_part, tomorrow_part = body.split("I morgen (")
        self.assertNotIn("00:00–06:00", today_part)  # klokken er 10: natten er forbi
        self.assertIn("12:00–15:00", today_part)
        self.assertIn("00:00–06:00", tomorrow_part)
        self.assertEqual(AlertLog.objects.filter(user=self.anna).count(), 2)

    def test_no_cheap_periods_means_no_mail_but_day_is_remembered(self):
        make_day(day=self.tomorrow, price=lambda h: 900)
        self.assertEqual(self.send(), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(AlertLog.objects.get(user=self.anna, date=self.tomorrow).sent)

    def test_waits_for_prices_and_for_complete_data(self):
        self.assertEqual(self.send(), 0)  # ingen priser endnu
        self.assertFalse(AlertLog.objects.exists())  # og dagen vurderes igen senere
        rows = make_day(day=self.tomorrow, price=cheap_pattern)
        SpotPrice.objects.filter(time__gte=rows[12].time).delete()  # kun halv dag hentet
        self.assertEqual(self.send(), 0)
        self.assertFalse(AlertLog.objects.exists())
        SpotPrice.objects.all().delete()
        make_day(day=self.tomorrow, price=cheap_pattern)  # nu er hele dagen hentet
        self.assertEqual(self.send(), 1)

    def test_vat_setting_decides_what_counts(self):
        make_day(day=self.tomorrow, price=cheap_pattern)
        self.alert.threshold_ore = 35  # 30 øre uden moms er billig, 37,5 med moms er ikke
        self.alert.save()
        self.send()
        self.assertEqual(mail.outbox[0].subject.count("perioder"), 0)  # kun nattens periode falder fra
        self.assertIn("12:00–15:00", mail.outbox[0].body)
        self.assertNotIn("00:00–06:00", mail.outbox[0].body)
        mail.outbox.clear()
        AlertLog.objects.all().delete()
        self.alert.incl_vat = False
        self.alert.save()
        self.send()
        self.assertIn("00:00–06:00", mail.outbox[0].body)

    def test_minimum_duration_filters_short_periods(self):
        make_day(day=self.tomorrow, price=cheap_pattern)
        self.alert.min_hours = 4
        self.alert.save()
        self.send()
        self.assertIn("00:00–06:00", mail.outbox[0].body)
        self.assertNotIn("12:00–15:00", mail.outbox[0].body)  # kun 3 timer

    def test_works_with_quarter_hour_data(self):
        make_day(day=self.tomorrow, step=15, price=cheap_pattern)
        self.send()
        self.assertIn("kl. 12:00–15:00 (3 timer)", mail.outbox[0].body)

    def test_uses_the_chosen_area(self):
        make_day("DK1", day=self.tomorrow, price=lambda h: 900)
        make_day("DK2", day=self.tomorrow, price=cheap_pattern)
        self.assertEqual(self.send(), 0)  # DK1 er dyrt
        self.alert.area = "DK2"
        self.alert.save()
        AlertLog.objects.all().delete()  # det gør formularen, når man ændrer indstillinger
        self.assertEqual(self.send(), 1)
        self.assertIn("(DK2)", mail.outbox[0].subject)

    def test_disabled_users_without_email_and_inactive_users_get_nothing(self):
        make_day(day=self.tomorrow, price=cheap_pattern)
        self.alert.enabled = False
        self.alert.save()
        self.assertEqual(self.send(), 0)
        self.alert.enabled = True
        self.alert.save()
        self.anna.email = ""
        self.anna.save()
        self.assertEqual(self.send(), 0)
        self.anna.email = "anna@example.dk"
        self.anna.is_active = False
        self.anna.save()
        self.assertEqual(self.send(), 0)
        self.assertEqual((len(mail.outbox), AlertLog.objects.count()), (0, 0))

    def test_each_user_gets_their_own_settings(self):
        make_day(day=self.tomorrow, price=cheap_pattern)
        CheapPowerAlert.objects.create(user=self.bo, enabled=True, threshold_ore=10, incl_vat=False)  # strengere grænse
        self.assertEqual(self.send(), 1)
        self.assertEqual([m.to[0] for m in mail.outbox], ["anna@example.dk"])
        self.assertEqual(AlertLog.objects.filter(user=self.bo, sent=False).count(), 1)

    def test_failure_does_not_block_others_and_is_retried(self):
        make_day(day=self.tomorrow, price=cheap_pattern)
        CheapPowerAlert.objects.create(user=self.bo, enabled=True, threshold_ore=40, incl_vat=True)
        real_send = mail.EmailMessage.send

        def flaky(message, *args, **kwargs):
            if message.to == ["anna@example.dk"]:
                raise OSError("SMTP nede")
            return real_send(message, *args, **kwargs)

        with mock.patch.object(mail.EmailMessage, "send", flaky), self.assertLogs("elpriser.alerts", "ERROR"):
            self.assertEqual(self.send(), 1)
        self.assertEqual([m.to[0] for m in mail.outbox], ["bo@example.dk"])
        self.assertFalse(AlertLog.objects.filter(user=self.anna).exists())  # ikke husket: prøves igen
        self.assertEqual(self.send(), 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_dry_run_changes_nothing(self):
        make_day(day=self.tomorrow, price=cheap_pattern)
        self.assertEqual(self.send(dry_run=True), 1)
        self.assertEqual((len(mail.outbox), AlertLog.objects.count()), (0, 0))

    def test_yesterdays_log_does_not_block_new_days(self):
        AlertLog.objects.create(user=self.anna, date=DAY - timedelta(days=1), sent=True)
        make_day(day=self.tomorrow, price=cheap_pattern)
        self.assertEqual(self.send(), 1)

    def test_saving_settings_makes_pending_days_be_evaluated_again(self):
        make_day(day=self.tomorrow, price=cheap_pattern)
        self.send()
        mail.outbox.clear()
        with mock.patch("django.utils.timezone.localdate", return_value=DAY):
            form = alerts_form(self.anna, self.alert, threshold_ore="40")
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
        self.assertEqual(AlertLog.objects.filter(user=self.anna).count(), 0)
        self.assertEqual(self.send(), 1)


ALERT_POST = {
    "enabled": "on", "area": "DK1", "notify_cheap": "on", "threshold_ore": "40", "incl_vat": "on", "min_hours": "1",
    "expensive_ore": "100", "notify_email": "on",
}


def alerts_form(user, instance, **overrides):
    from .forms import AlertForm

    data = {**ALERT_POST, "email": user.email, **overrides}
    data = {k: v for k, v in data.items() if v is not None}  # None = feltet udelades (som en fravalgt afkrydsning)
    return AlertForm(data, instance=instance, user=user)


class AlertFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna", password="pw", email="anna@example.dk")

    def form(self, **overrides):
        return alerts_form(self.anna, CheapPowerAlert(user=self.anna), **overrides)

    def test_valid_and_saves_email(self):
        form = self.form(email="ny@example.dk", threshold_ore="32.5")
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.anna.refresh_from_db()
        self.assertEqual(self.anna.email, "ny@example.dk")
        self.assertEqual(CheapPowerAlert.objects.get().threshold_ore, Decimal("32.50"))

    def test_enabling_requires_email(self):
        self.assertIn("email", self.form(email="").errors)
        self.assertTrue(self.form(email="", enabled=None).is_valid())

    def test_invalid_values(self):
        for overrides in ({"threshold_ore": "mange"}, {"threshold_ore": "5000"}, {"threshold_ore": "-500"}, {"area": "SE3"}, {"min_hours": "9"}, {"email": "ikke-en-mail"}):
            self.assertFalse(self.form(**overrides).is_valid(), overrides)

    def test_negative_threshold_is_allowed(self):
        self.assertTrue(self.form(threshold_ore="-5").is_valid())

    def test_default_area_is_west_of_great_belt(self):
        self.assertEqual(CheapPowerAlert().area, "DK1")


@mock.patch("elpriser.views.local_today", return_value=DAY)
class AlertPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna", password="pw", email="anna@example.dk")

    def setUp(self):
        mail.outbox.clear()

    def test_login_required(self, _):
        for name in ("alerts", "alerts_test"):
            response = self.client.get(reverse(f"elpriser:{name}")) if name == "alerts" else self.client.post(reverse(f"elpriser:{name}"))
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("accounts:login"), response["Location"])

    def test_page_defaults_to_dk1_and_is_disabled(self, _):
        self.client.force_login(self.anna)
        response = self.client.get(reverse("elpriser:alerts"))
        self.assertContains(response, "vest for Storebælt")
        self.assertContains(response, '<option value="DK1" selected>')
        self.assertFalse(CheapPowerAlert.objects.get(user=self.anna).enabled)

    def test_save_settings(self, _):
        self.client.force_login(self.anna)
        response = self.client.post(reverse("elpriser:alerts"), {
            "enabled": "on", "area": "DK1", "notify_cheap": "on", "notify_email": "on", "threshold_ore": "35", "min_hours": "2",
            "expensive_ore": "100", "email": "anna@example.dk"})
        self.assertRedirects(response, reverse("elpriser:alerts"))
        alert = CheapPowerAlert.objects.get(user=self.anna)
        self.assertEqual((alert.enabled, alert.area, alert.threshold_ore, alert.incl_vat, alert.min_hours), (True, "DK1", 35, False, 2))

    def test_invalid_settings_show_errors(self, _):
        self.client.force_login(self.anna)
        response = self.client.post(reverse("elpriser:alerts"), {**ALERT_POST, "email": ""})
        self.assertContains(response, "Angiv en e-mailadresse", status_code=400)
        self.assertFalse(CheapPowerAlert.objects.get(user=self.anna).enabled)

    def test_settings_are_private_per_user(self, _):
        self.client.force_login(self.anna)
        self.client.post(reverse("elpriser:alerts"), {**ALERT_POST, "area": "DK2", "threshold_ore": "10", "email": "a@b.dk"})
        other = User.objects.create_user("bo", password="pw")
        self.client.force_login(other)
        self.assertContains(self.client.get(reverse("elpriser:alerts")), '<option value="DK1" selected>')

    def test_preview_shows_periods_and_share(self, _):
        make_day(day=DAY, price=cheap_pattern)
        make_day(day=DAY - timedelta(days=1), price=lambda h: 900)
        self.client.force_login(self.anna)
        response = self.client.get(reverse("elpriser:alerts"))
        preview = response.context["preview"]
        by_label = {d["label"]: d for d in preview["days"]}
        self.assertEqual(hours_of(by_label["I dag"]["cheap"]), [("00:00", "06:00"), ("12:00", "15:00")])
        self.assertEqual(by_label["I dag"]["cheap"][0].avg, 37.5)
        self.assertEqual(by_label["I dag"]["expensive"], [])  # advarsel om dyrt er ikke slået til
        self.assertEqual(len([d for d in preview["days"]]), 2)  # kun dage med data
        self.assertEqual(by_label.keys() - {"I dag"}, {"søn 14/06"})  # dansk ugedag (14. juni 2026 er en søndag)
        self.assertEqual(preview["share"], round(100 * 9 / 48))  # 9 af 48 timer er billige (<40 øre inkl. moms)
        self.assertContains(response, "kl. 00:00–06:00")

    def test_preview_without_data(self, _):
        self.client.force_login(self.anna)
        response = self.client.get(reverse("elpriser:alerts"))
        self.assertContains(response, "ingen priser i databasen")
        self.assertIsNone(response.context["preview"]["share"])

    def test_test_mail(self, _):
        self.client.force_login(self.anna)
        self.assertRedirects(self.client.post(reverse("elpriser:alerts_test")), reverse("elpriser:alerts"))
        self.assertEqual(mail.outbox[0].to, ["anna@example.dk"])
        self.assertEqual(self.client.get(reverse("elpriser:alerts_test")).status_code, 405)

    def test_test_mail_without_address_or_on_failure(self, _):
        self.client.force_login(self.anna)
        self.anna.email = ""
        self.anna.save()
        self.client.post(reverse("elpriser:alerts_test"))
        self.assertEqual(len(mail.outbox), 0)
        self.anna.email = "anna@example.dk"
        self.anna.save()
        with mock.patch.object(mail.EmailMessage, "send", side_effect=OSError("nede")):
            self.assertContains(self.client.post(reverse("elpriser:alerts_test"), follow=True), "Mailen kunne ikke sendes")

    def test_index_shows_alert_button_state(self, _):
        self.assertContains(self.client.get(reverse("elpriser:index")), "Få besked om billig strøm")
        self.client.force_login(self.anna)
        self.assertContains(self.client.get(reverse("elpriser:index")), "Få besked om billig strøm")
        CheapPowerAlert.objects.create(user=self.anna, enabled=True)
        self.assertContains(self.client.get(reverse("elpriser:index")), "Besked til (DK1)")


class AlertCommandTests(TestCase):
    def test_command_reports_count(self):
        out = io.StringIO()
        with mock.patch("elpriser.management.commands.send_cheap_power_alerts.send_due_alerts", return_value=2) as send:
            call_command("send_cheap_power_alerts", stdout=out)
        send.assert_called_once_with(dry_run=False)
        self.assertIn("2 besked(er) sendt", out.getvalue())
        out = io.StringIO()
        with mock.patch("elpriser.management.commands.send_cheap_power_alerts.send_due_alerts", return_value=1):
            call_command("send_cheap_power_alerts", "--dry-run", stdout=out)
        self.assertIn("ville blive sendt", out.getvalue())

    def test_fetch_with_notify_sends_alerts_after_fetching_only(self):
        order = []
        with mock.patch("elpriser.management.commands.fetch_elpriser.update_prices", side_effect=lambda **k: order.append("hent") or service.UpdateResult(1, 0)), \
             mock.patch("elpriser.management.commands.fetch_elpriser.send_due_alerts", side_effect=lambda: order.append("besked") or 3):
            out = io.StringIO()
            call_command("fetch_elpriser", "--notify", stdout=out)
            self.assertEqual(order, ["hent", "besked"])
            self.assertIn("3 besked(er)", out.getvalue())
            order.clear()
            call_command("fetch_elpriser", stdout=io.StringIO())
            self.assertEqual(order, ["hent"])

    def test_failed_fetch_does_not_notify(self):
        with mock.patch("elpriser.management.commands.fetch_elpriser.update_prices", side_effect=EnergiDataError("nede")), \
             mock.patch("elpriser.management.commands.fetch_elpriser.send_due_alerts") as send:
            with self.assertRaises(CommandError):
                call_command("fetch_elpriser", "--notify", stdout=io.StringIO())
        send.assert_not_called()


# --- Dyre perioder og besked til telefonen (ntfy) ------------------------------------------------

from django.test import override_settings  # noqa: E402

from . import push  # noqa: E402


def expensive_pattern(hour):
    """DKK/MWh pr. dansk time: 0–5 → 300 (30 øre), 6–11 → 800 (80), 12–14 → 200 (20), 15–19 → 1600 (160), 20–23 → 900 (90)."""
    return 300 if hour < 6 else 800 if hour < 12 else 200 if hour < 15 else 1600 if hour < 20 else 900


class ExpensivePeriodsTests(SimpleTestCase):
    def find(self, prices, threshold=100, vat=False, minutes=60):
        points = [(utc(2026, 6, 15, 0) + timedelta(hours=i), float(p)) for i, p in enumerate(prices)]
        return alerts.find_periods(points, 60, threshold, vat, minutes, above=True)

    def test_above_is_strict(self):
        self.assertEqual(self.find([1000]), [])  # præcis 100 øre er ikke «over»
        self.assertEqual(len(self.find([1001])), 1)

    def test_periods_stats_and_gaps(self):
        periods = self.find([500, 1200, 1600, 500, 1500])
        self.assertEqual([(p.start.hour, p.end.hour) for p in periods], [(1, 3), (4, 5)])
        self.assertEqual((periods[0].avg, periods[0].low, periods[0].high), (140.0, 120.0, 160.0))

    def test_vat_and_minimum_duration(self):
        self.assertEqual(len(self.find([850], vat=False)), 0)  # 85
        self.assertEqual(len(self.find([850], vat=True)), 1)  # 106,25 med moms
        self.assertEqual(self.find([1200, 500, 1200], minutes=120), [])

    def test_cheap_and_expensive_never_overlap(self):
        prices = [300, 900, 1500, 200]
        points = [(utc(2026, 6, 15, 0) + timedelta(hours=i), float(p)) for i, p in enumerate(prices)]
        cheap = alerts.find_periods(points, 60, 40, False, 60)
        expensive = alerts.find_periods(points, 60, 100, False, 60, above=True)
        taken = {p.start for p in cheap}
        self.assertFalse(taken & {p.start for p in expensive})


class DayReportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("anna", password="pw", email="anna@example.dk")
        make_day(price=expensive_pattern)

    def report(self, **fields):
        alert = CheapPowerAlert(user=self.user, area="DK1", threshold_ore=40, expensive_ore=100, incl_vat=False, **fields)
        return alerts.day_report(alert, DAY)

    def test_both_kinds(self):
        report = self.report(notify_cheap=True, warn_expensive=True)
        self.assertEqual(hours_of(report.cheap), [("00:00", "06:00"), ("12:00", "15:00")])
        self.assertEqual(hours_of(report.expensive), [("15:00", "20:00")])
        self.assertTrue(report.has_any)

    def test_toggles_are_respected(self):
        only_cheap = self.report(notify_cheap=True, warn_expensive=False)
        self.assertEqual(only_cheap.expensive, [])
        only_expensive = self.report(notify_cheap=False, warn_expensive=True)
        self.assertEqual(only_expensive.cheap, [])
        self.assertEqual(len(only_expensive.expensive), 1)

    def test_none_when_day_missing(self):
        alert = CheapPowerAlert(user=self.user, area="DK1")
        self.assertIsNone(alerts.day_report(alert, DAY + timedelta(days=5)))


class MultiChannelAlertTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna", password="pw", email="anna@example.dk")

    def setUp(self):
        mail.outbox.clear()
        self.tomorrow = DAY + timedelta(days=1)
        make_day(day=self.tomorrow, price=expensive_pattern)

    def alert(self, **fields):
        defaults = dict(user=self.anna, enabled=True, threshold_ore=40, expensive_ore=100, incl_vat=False,
                        notify_cheap=True, warn_expensive=True, notify_email=True, notify_phone=False)
        return CheapPowerAlert.objects.create(**{**defaults, **fields})

    def send(self, **kwargs):
        return alerts.send_due_alerts(now=NOW, **kwargs)

    def test_email_contains_both_sections_and_subject_names_both(self):
        self.alert()
        self.assertEqual(self.send(), 1)
        message = mail.outbox[0]
        self.assertEqual(message.subject, "Billig og dyr strøm i morgen (DK1): billigst kl. 12:00–15:00, dyrest kl. 15:00–20:00")
        self.assertIn("Billigt (under 40,0 øre/kWh ekskl. moms):", message.body)
        self.assertIn("Dyrt (over 100,0 øre/kWh ekskl. moms):", message.body)
        self.assertIn("kl. 15:00–20:00 (5 timer): gennemsnit 160,0 øre/kWh, højeste 160,0", message.body)

    def test_expensive_only_subjects(self):
        self.alert(notify_cheap=False)
        self.send()
        self.assertEqual(mail.outbox[0].subject, "Dyr strøm i morgen (DK1): kl. 15:00–20:00 · 160,0 øre/kWh")
        self.assertNotIn("Billigt", mail.outbox[0].body)
        mail.outbox.clear()
        AlertLog.objects.all().delete()
        CheapPowerAlert.objects.update(expensive_ore=50)  # nu er både 80 og 160 øre dyrt
        self.send()
        # 15–20 (160 øre) og 20–24 (90 øre) ligger op ad hinanden og er begge dyre, så de er én periode
        self.assertIn("2 perioder, dyrest kl. 15:00–00:00", mail.outbox[0].subject)

    def test_expensive_warning_alone_is_enough_to_trigger(self):
        make_day(day=self.tomorrow + timedelta(days=1), price=lambda h: 1600)  # kun dyrt
        self.alert(notify_cheap=True, warn_expensive=True)
        SpotPrice.objects.filter(time__lt=analysis.day_bounds(self.tomorrow + timedelta(days=1))[0]).delete()
        alerts.send_due_alerts(now=utc(2026, 6, 16, 8, 0))
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(mail.outbox[0].subject.startswith("Dyr strøm"))

    def test_phone_only(self):
        self.alert(notify_email=False, notify_phone=True, ntfy_topic="elpris-abcdefghij")
        with mock.patch("elpriser.alerts.push.send_ntfy") as send_ntfy:
            self.assertEqual(self.send(), 1)
        self.assertEqual(len(mail.outbox), 0)
        (topic, title, message), kwargs = send_ntfy.call_args
        self.assertEqual(topic, "elpris-abcdefghij")
        self.assertTrue(title.startswith("Billig og dyr strøm i morgen (DK1)"))
        self.assertIn("I morgen: billigt kl. 00:00–06:00 (30,0 øre), kl. 12:00–15:00 (20,0 øre); dyrt kl. 15:00–20:00 (160,0 øre)", message)
        self.assertIn("dato=2026-06-16", kwargs["click"])
        self.assertEqual(kwargs["tags"], ["zap"])

    def test_expensive_only_push_uses_warning_tag(self):
        self.alert(notify_cheap=False, notify_email=False, notify_phone=True, ntfy_topic="elpris-abcdefghij")
        with mock.patch("elpriser.alerts.push.send_ntfy") as send_ntfy:
            self.send()
        self.assertEqual(send_ntfy.call_args.kwargs["tags"], ["warning"])

    def test_both_channels(self):
        self.alert(notify_phone=True, ntfy_topic="elpris-abcdefghij")
        with mock.patch("elpriser.alerts.push.send_ntfy") as send_ntfy:
            self.assertEqual(self.send(), 1)  # én bruger fik besked
        self.assertEqual((len(mail.outbox), send_ntfy.call_count), (1, 1))

    def test_one_failing_channel_does_not_block_the_other_and_day_is_remembered(self):
        self.alert(notify_phone=True, ntfy_topic="elpris-abcdefghij")
        with mock.patch("elpriser.alerts.push.send_ntfy", side_effect=push.PushError("nede")), self.assertLogs("elpriser.alerts", "ERROR"):
            self.assertEqual(self.send(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(AlertLog.objects.filter(user=self.anna, date=self.tomorrow).exists())  # ingen dobbelt-mail senere

    def test_all_channels_failing_means_retry_next_time(self):
        self.alert(notify_phone=True, ntfy_topic="elpris-abcdefghij")
        with mock.patch("elpriser.alerts.push.send_ntfy", side_effect=push.PushError("nede")), \
             mock.patch.object(mail.EmailMessage, "send", side_effect=OSError("nede")), self.assertLogs("elpriser.alerts", "ERROR"):
            self.assertEqual(self.send(), 0)
        self.assertFalse(AlertLog.objects.exists())
        with mock.patch("elpriser.alerts.push.send_ntfy"):
            self.assertEqual(self.send(), 1)

    def test_eligibility_rules(self):
        alert = self.alert(notify_email=False, notify_phone=True, ntfy_topic="")  # telefon uden emne
        self.assertEqual(self.send(), 0)
        alert.ntfy_topic = "elpris-abcdefghij"
        alert.notify_phone = False
        alert.save()
        self.assertEqual(self.send(), 0)  # hverken e-mail eller telefon valgt
        alert.notify_email = True
        alert.notify_cheap = alert.warn_expensive = False
        alert.save()
        self.assertEqual(self.send(), 0)  # intet at give besked om
        self.assertEqual(len(mail.outbox), 0)

    def test_phone_user_without_email_address_is_served(self):
        self.anna.email = ""
        self.anna.save()
        self.alert(notify_email=False, notify_phone=True, ntfy_topic="elpris-abcdefghij")
        with mock.patch("elpriser.alerts.push.send_ntfy") as send_ntfy:
            self.assertEqual(self.send(), 1)
        send_ntfy.assert_called_once()

    def test_dry_run_sends_nothing_on_any_channel(self):
        self.alert(notify_phone=True, ntfy_topic="elpris-abcdefghij")
        with mock.patch("elpriser.alerts.push.send_ntfy") as send_ntfy:
            self.assertEqual(self.send(dry_run=True), 1)
        self.assertEqual((len(mail.outbox), send_ntfy.call_count, AlertLog.objects.count()), (0, 0, 0))


@override_settings(NTFY_SERVER="https://ntfy.example.org/", NTFY_TOKEN="")
class PushTests(SimpleTestCase):
    def send(self, topic="elpris-abcdefghij", **kwargs):
        opener = fake_urlopen({"id": "x"})
        with mock.patch("elpriser.push.urllib.request.urlopen", opener):
            push.send_ntfy(topic, "Titel – med æøå", "Besked", **kwargs)
        return opener.call_args

    def test_posts_json_to_the_configured_server_only(self):
        (request,), kwargs = self.send(click="https://x.dk/", tags=["zap"], priority=4)
        self.assertEqual(request.full_url, "https://ntfy.example.org/")
        self.assertEqual(request.get_method(), "POST")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload, {"topic": "elpris-abcdefghij", "title": "Titel – med æøå", "message": "Besked",
                                   "priority": 4, "tags": ["zap"], "click": "https://x.dk/"})
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(kwargs["timeout"], 15)

    @override_settings(NTFY_TOKEN="hemmelig")
    def test_token_is_sent_when_configured(self):
        (request,), _ = self.send()
        self.assertEqual(request.get_header("Authorization"), "Bearer hemmelig")

    def test_invalid_topics_are_refused_before_any_request(self):
        for bad in ("", "kort", "med/skråstreg-123456", "../etc/passwd", "a" * 65, "mellem rum 123", None, "https://ond.dk/x12345678"):
            with mock.patch("elpriser.push.urllib.request.urlopen") as opener, self.assertRaises(push.PushError):
                push.send_ntfy(bad, "t", "m")
            opener.assert_not_called()

    def test_errors_become_pusherror(self):
        for error, text in ((http_error(403), "HTTP 403"), (urllib.error.URLError("ingen forbindelse"), "ingen forbindelse"), (TimeoutError("langsom"), "langsom")):
            with mock.patch("elpriser.push.urllib.request.urlopen", mock.Mock(side_effect=error)):
                with self.assertRaisesMessage(push.PushError, text):
                    push.send_ntfy("elpris-abcdefghij", "t", "m")

    def test_new_topics_are_random_valid_and_prefixed(self):
        topics = {push.new_topic() for _ in range(50)}
        self.assertEqual(len(topics), 50)
        self.assertTrue(all(push.TOPIC_RE.match(t) and t.startswith("elpris-") for t in topics))

    def test_topic_url(self):
        self.assertEqual(push.topic_url("elpris-abcdefghij"), "https://ntfy.example.org/elpris-abcdefghij")


class AlertChannelFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna", password="pw", email="anna@example.dk")
        cls.bo = User.objects.create_user("bo", password="pw", email="bo@example.dk")

    def form(self, user=None, instance=None, **overrides):
        user = user or self.anna
        return alerts_form(user, instance or CheapPowerAlert.objects.get_or_create(user=user)[0], **overrides)

    def test_topic_generated_once_when_phone_is_enabled_and_kept(self):
        first = self.form(notify_phone="on")
        self.assertTrue(first.is_valid(), first.errors)
        topic = first.save().ntfy_topic
        self.assertTrue(push.TOPIC_RE.match(topic))
        again = self.form(notify_phone="on", instance=CheapPowerAlert.objects.get(user=self.anna))
        again.is_valid()
        self.assertEqual(again.save().ntfy_topic, topic)
        off = self.form(instance=CheapPowerAlert.objects.get(user=self.anna))  # telefon fra: emnet bevares
        off.is_valid()
        self.assertEqual(off.save().ntfy_topic, topic)

    def test_no_topic_when_phone_is_never_enabled(self):
        form = self.form()
        form.is_valid()
        self.assertEqual(form.save().ntfy_topic, "")

    def test_users_get_different_topics(self):
        a, b = self.form(notify_phone="on"), self.form(user=self.bo, notify_phone="on")
        a.is_valid(), b.is_valid()
        self.assertNotEqual(a.save().ntfy_topic, b.save().ntfy_topic)

    def test_topic_cannot_be_set_through_the_form(self):
        form = self.form(notify_phone="on", ntfy_topic="andres-emne-123456")
        form.is_valid()
        self.assertNotEqual(form.save().ntfy_topic, "andres-emne-123456")

    def test_validation_rules(self):
        self.assertIn("højere end", " ".join(self.form(warn_expensive="on", expensive_ore="30").errors["expensive_ore"]))
        self.assertIn("højere end", " ".join(self.form(warn_expensive="on", expensive_ore="40").errors["expensive_ore"]))
        self.assertTrue(self.form(warn_expensive="on", expensive_ore="41").is_valid())
        # grænserne må gerne «krydse», når kun én slags besked er valgt
        self.assertTrue(self.form(warn_expensive="on", notify_cheap=None, expensive_ore="10").is_valid())
        self.assertFalse(self.form(notify_cheap=None).is_valid())  # hverken billig eller dyr
        self.assertFalse(self.form(notify_email=None).is_valid())  # ingen kanal
        self.assertTrue(self.form(notify_email=None, notify_phone="on", email="").is_valid())  # kun telefon: e-mail ikke nødvendig
        self.assertIn("email", self.form(email="").errors)  # e-mail valgt uden adresse

    def test_nothing_is_required_while_disabled(self):
        self.assertTrue(self.form(enabled=None, notify_cheap=None, notify_email=None, email="").is_valid())


@mock.patch("elpriser.views.local_today", return_value=DAY)
class PhonePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.anna = User.objects.create_user("anna", password="pw", email="anna@example.dk")
        cls.bo = User.objects.create_user("bo", password="pw", email="bo@example.dk")

    def save_phone(self, user):
        self.client.force_login(user)
        self.client.post(reverse("elpriser:alerts"), {**ALERT_POST, "notify_phone": "on", "email": user.email})
        return CheapPowerAlert.objects.get(user=user).ntfy_topic

    def test_page_shows_setup_instructions_only_to_the_owner(self, _):
        topic = self.save_phone(self.anna)
        page = self.client.get(reverse("elpriser:alerts"))
        self.assertContains(page, "Sådan får du beskeder på telefonen")
        self.assertContains(page, topic)
        self.client.force_login(self.bo)
        other = self.client.get(reverse("elpriser:alerts"))
        self.assertNotContains(other, topic)
        self.assertNotContains(other, "Sådan får du beskeder på telefonen")

    def test_phone_test_notification(self, _):
        topic = self.save_phone(self.anna)
        with mock.patch("elpriser.views.push.send_ntfy") as send_ntfy:
            response = self.client.post(reverse("elpriser:alerts_test_phone"), follow=True)
        self.assertEqual(send_ntfy.call_args.args[0], topic)
        self.assertContains(response, "Testbesked sendt")

    def test_phone_test_failure_and_missing_topic(self, _):
        self.client.force_login(self.anna)
        self.assertContains(self.client.post(reverse("elpriser:alerts_test_phone"), follow=True), "Slå «besked til telefonen» til")
        self.save_phone(self.anna)
        with mock.patch("elpriser.views.push.send_ntfy", side_effect=push.PushError("ntfy svarede HTTP 429.")):
            self.assertContains(self.client.post(reverse("elpriser:alerts_test_phone"), follow=True), "HTTP 429")

    def test_new_topic_replaces_old_and_is_post_only_and_private(self, _):
        old = self.save_phone(self.anna)
        bos = self.save_phone(self.bo)
        self.client.force_login(self.anna)
        self.assertEqual(self.client.get(reverse("elpriser:alerts_new_topic")).status_code, 405)
        self.client.post(reverse("elpriser:alerts_new_topic"))
        new = CheapPowerAlert.objects.get(user=self.anna).ntfy_topic
        self.assertNotEqual(new, old)
        self.assertTrue(push.TOPIC_RE.match(new))
        self.assertEqual(CheapPowerAlert.objects.get(user=self.bo).ntfy_topic, bos)

    def test_login_required_for_phone_endpoints(self, _):
        for name in ("alerts_test_phone", "alerts_new_topic"):
            response = self.client.post(reverse(f"elpriser:{name}"))
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("accounts:login"), response["Location"])

    def test_preview_shows_expensive_periods_and_share(self, _):
        make_day(day=DAY, price=expensive_pattern)
        self.client.force_login(self.anna)
        self.client.post(reverse("elpriser:alerts"), {**ALERT_POST, "incl_vat": "", "warn_expensive": "on", "expensive_ore": "100", "email": self.anna.email})
        page = self.client.get(reverse("elpriser:alerts"))
        today = {d["label"]: d for d in page.context["preview"]["days"]}["I dag"]
        self.assertEqual(hours_of(today["expensive"]), [("15:00", "20:00")])
        self.assertEqual(page.context["preview"]["expensive_share"], round(100 * 5 / 24))
        self.assertContains(page, "dyr")
