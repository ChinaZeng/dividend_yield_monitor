import json
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from data_sources import DataSourceError, DividendAction, Quote
from monitor import check_prepared_run, prepare_run


TIMEZONE = ZoneInfo("Asia/Shanghai")


class FakeQuoteClient:
    def __init__(self, quotes=None, error=None):
        self.quotes = quotes or {}
        self.error = error
        self.requested = None

    def fetch_quotes(self, codes):
        self.requested = list(codes)
        if self.error:
            raise self.error
        return self.quotes


class FakeDividendClient:
    def __init__(self, actions=None, errors=None):
        self.actions = actions or {}
        self.errors = errors or {}

    def fetch_actions(self, code):
        if code in self.errors:
            raise self.errors[code]
        return self.actions.get(code, [])


def quote(code, name, timestamp, price="10"):
    return Quote(
        code=code,
        name=name,
        price=Decimal(price),
        previous_close=Decimal("10.1"),
        quote_time=datetime.fromisoformat(timestamp).replace(tzinfo=TIMEZONE),
    )


def cash_action(cash_per_10="5"):
    return DividendAction(
        report_period="2025-12-31",
        ex_date=date(2026, 5, 13),
        status="实施分配",
        cash_per_10_shares=Decimal(cash_per_10),
        bonus_shares_per_10=Decimal("0"),
        transfer_shares_per_10=Decimal("0"),
        description="10派5元(含税)",
    )


class MonitorIntegrationTest(unittest.TestCase):
    def _config(self, root, stocks=None):
        stocks = stocks or [
            {"code": "601288.SH", "name": "农业银行", "yield_threshold_pct": 5.0}
        ]
        path = Path(root) / "config.json"
        path.write_text(
            json.dumps(
                {
                    "timezone": "Asia/Shanghai",
                    "stocks": stocks,
                    "notifier": {
                        "type": "qqmail",
                        "address_env": "QQ_EMAIL_ADDRESS",
                        "auth_code_env": "QQ_EMAIL_AUTH_CODE",
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_prepare_writes_snapshot_markdown_png_and_ok_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            config = self._config(root)
            quotes = {
                "000001.SH": quote("000001.SH", "上证指数", "2026-08-14T15:01:00", "3200"),
                "601288.SH": quote("601288.SH", "农业银行", "2026-08-14T15:02:00", "10"),
            }
            manifest_path = prepare_run(
                config,
                Path(root) / "report",
                snapshot_dir=Path(root) / "data",
                quote_client=FakeQuoteClient(quotes),
                dividend_client=FakeDividendClient({"601288.SH": [cash_action()]}),
                now=datetime(2026, 8, 14, 16, 30, tzinfo=TIMEZONE),
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(manifest["status"], "ok")
            self.assertEqual(check_prepared_run(manifest_path), 0)
            self.assertTrue(Path(manifest["snapshot_path"]).exists())
            self.assertTrue(Path(manifest["markdown_path"]).exists())
            self.assertTrue(Path(manifest["image_path"]).exists())
            snapshot = json.loads(Path(manifest["snapshot_path"]).read_text(encoding="utf-8"))
            self.assertEqual(snapshot["stocks"][0]["recommendation"], "buy_candidate")

    def test_non_trading_day_only_writes_skipped_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            config = self._config(root)
            quotes = {
                "000001.SH": quote("000001.SH", "上证指数", "2026-08-13T15:01:00", "3200")
            }
            output = Path(root) / "report"
            snapshot_dir = Path(root) / "data"
            manifest_path = prepare_run(
                config,
                output,
                snapshot_dir=snapshot_dir,
                quote_client=FakeQuoteClient(quotes),
                dividend_client=FakeDividendClient(),
                now=datetime(2026, 8, 14, 16, 30, tzinfo=TIMEZONE),
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(manifest["status"], "skipped_non_trading_day")
            self.assertEqual(check_prepared_run(manifest_path), 0)
            self.assertFalse(snapshot_dir.exists())
            self.assertEqual(list(output.glob("*.png")), [])

    def test_sell_threshold_produces_sell_candidate(self):
        stocks = [
            {
                "code": "601288.SH",
                "name": "农业银行",
                "buy_yield_threshold_pct": 5.0,
                "sell_yield_threshold_pct": 3.0,
            }
        ]
        with tempfile.TemporaryDirectory() as root:
            config = self._config(root, stocks)
            quotes = {
                "000001.SH": quote("000001.SH", "上证指数", "2026-08-14T15:01:00", "3200"),
                "601288.SH": quote("601288.SH", "农业银行", "2026-08-14T15:02:00", "10"),
            }
            manifest_path = prepare_run(
                config,
                Path(root) / "report",
                snapshot_dir=Path(root) / "data",
                quote_client=FakeQuoteClient(quotes),
                dividend_client=FakeDividendClient(
                    {"601288.SH": [cash_action("3")]}
                ),
                now=datetime(2026, 8, 14, 16, 30, tzinfo=TIMEZONE),
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            snapshot = json.loads(
                Path(manifest["snapshot_path"]).read_text(encoding="utf-8")
            )

            self.assertEqual(snapshot["stocks"][0]["recommendation"], "sell_candidate")

    def test_same_day_rerun_overwrites_the_same_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            config = self._config(root)
            snapshot_dir = Path(root) / "data"
            first_quotes = {
                "000001.SH": quote("000001.SH", "上证指数", "2026-08-14T15:01:00", "3200"),
                "601288.SH": quote("601288.SH", "农业银行", "2026-08-14T15:02:00", "10"),
            }
            second_quotes = {
                **first_quotes,
                "601288.SH": quote("601288.SH", "农业银行", "2026-08-14T15:03:00", "8"),
            }
            common = {
                "snapshot_dir": snapshot_dir,
                "dividend_client": FakeDividendClient({"601288.SH": [cash_action()]}),
                "now": datetime(2026, 8, 14, 16, 30, tzinfo=TIMEZONE),
            }
            first_manifest = prepare_run(
                config,
                Path(root) / "report-first",
                quote_client=FakeQuoteClient(first_quotes),
                **common,
            )
            first_snapshot_path = json.loads(first_manifest.read_text(encoding="utf-8"))[
                "snapshot_path"
            ]
            second_manifest = prepare_run(
                config,
                Path(root) / "report-second",
                quote_client=FakeQuoteClient(second_quotes),
                **common,
            )
            second_snapshot_path = json.loads(second_manifest.read_text(encoding="utf-8"))[
                "snapshot_path"
            ]
            snapshot = json.loads(Path(second_snapshot_path).read_text(encoding="utf-8"))

            self.assertEqual(first_snapshot_path, second_snapshot_path)
            self.assertEqual(snapshot["stocks"][0]["close"], 8.0)
            self.assertEqual(snapshot["stocks"][0]["signal_event"], "maintain")
            self.assertEqual(snapshot["stocks"][0]["model_position_pct"], 100.0)

    def test_sell_level_never_increases_a_smaller_model_position(self):
        stocks = [
            {
                "code": "601288.SH",
                "name": "农业银行",
                "buy_levels": [
                    {"yield_threshold_pct": 5, "target_position_pct": 25},
                    {"yield_threshold_pct": 5.5, "target_position_pct": 60},
                    {"yield_threshold_pct": 6, "target_position_pct": 100},
                ],
                "sell_levels": [
                    {"yield_threshold_pct": 4, "target_position_pct": 70},
                    {"yield_threshold_pct": 3.5, "target_position_pct": 35},
                    {"yield_threshold_pct": 3, "target_position_pct": 0},
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as root:
            config = self._config(root, stocks)
            snapshot_dir = Path(root) / "data"
            first_quotes = {
                "000001.SH": quote("000001.SH", "上证指数", "2026-08-14T15:01:00", "3200"),
                "601288.SH": quote("601288.SH", "农业银行", "2026-08-14T15:02:00", "10"),
            }
            second_quotes = {
                **first_quotes,
                "601288.SH": quote("601288.SH", "农业银行", "2026-08-14T15:03:00", "15"),
            }
            common = {
                "snapshot_dir": snapshot_dir,
                "dividend_client": FakeDividendClient({"601288.SH": [cash_action()]}),
                "now": datetime(2026, 8, 14, 16, 30, tzinfo=TIMEZONE),
            }
            prepare_run(
                config,
                Path(root) / "report-first",
                quote_client=FakeQuoteClient(first_quotes),
                **common,
            )
            second_manifest = prepare_run(
                config,
                Path(root) / "report-second",
                quote_client=FakeQuoteClient(second_quotes),
                **common,
            )
            manifest = json.loads(second_manifest.read_text(encoding="utf-8"))
            snapshot = json.loads(
                Path(manifest["snapshot_path"]).read_text(encoding="utf-8")
            )
            row = snapshot["stocks"][0]

            self.assertEqual(row["signal_key"], "sell_2")
            self.assertEqual(row["signal_target_position_pct"], 35.0)
            self.assertEqual(row["model_position_pct"], 25.0)
            self.assertEqual(row["position_action"], "hold")

    def test_partial_report_is_generated_and_health_check_fails(self):
        stocks = [
            {"code": "601288.SH", "name": "农业银行", "yield_threshold_pct": 5.0},
            {"code": "000001.SZ", "name": "平安银行", "yield_threshold_pct": 4.0},
        ]
        with tempfile.TemporaryDirectory() as root:
            config = self._config(root, stocks)
            quotes = {
                "000001.SH": quote("000001.SH", "上证指数", "2026-08-14T15:01:00", "3200"),
                "601288.SH": quote("601288.SH", "农业银行", "2026-08-14T15:02:00", "10"),
            }
            manifest_path = prepare_run(
                config,
                Path(root) / "report",
                snapshot_dir=Path(root) / "data",
                quote_client=FakeQuoteClient(quotes),
                dividend_client=FakeDividendClient({"601288.SH": [cash_action()]}),
                now=datetime(2026, 8, 14, 16, 30, tzinfo=TIMEZONE),
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            snapshot = json.loads(Path(manifest["snapshot_path"]).read_text(encoding="utf-8"))

            self.assertEqual(manifest["status"], "partial")
            self.assertEqual(check_prepared_run(manifest_path), 1)
            self.assertEqual(snapshot["stocks"][1]["recommendation"], "data_error")

    def test_market_probe_failure_generates_failure_report_without_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            config = self._config(root)
            manifest_path = prepare_run(
                config,
                Path(root) / "report",
                snapshot_dir=Path(root) / "data",
                quote_client=FakeQuoteClient(error=DataSourceError("network down")),
                dividend_client=FakeDividendClient(),
                now=datetime(2026, 8, 14, 16, 30, tzinfo=TIMEZONE),
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(manifest["status"], "failed")
            self.assertIsNone(manifest["snapshot_path"])
            self.assertTrue(Path(manifest["image_path"]).exists())
            self.assertEqual(check_prepared_run(manifest_path), 1)


if __name__ == "__main__":
    unittest.main()
