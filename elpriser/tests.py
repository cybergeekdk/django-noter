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
