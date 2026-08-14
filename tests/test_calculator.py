import unittest
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from calculator import calculate_stock_snapshot, subtract_calendar_year
from config_loader import PositionLevel, StockConfig
from data_sources import DividendAction, Quote


def action(
    ex_date,
    cash="0",
    bonus="0",
    transfer="0",
    status="实施分配",
    report_period="2025-12-31",
):
    return DividendAction(
        report_period=report_period,
        ex_date=ex_date,
        status=status,
        cash_per_10_shares=Decimal(cash),
        bonus_shares_per_10=Decimal(bonus),
        transfer_shares_per_10=Decimal(transfer),
        description=None,
    )


class CalculatorTest(unittest.TestCase):
    def setUp(self):
        self.stock = StockConfig(
            "601288.SH",
            "农业银行",
            (PositionLevel(Decimal("5"), Decimal("100")),),
            (),
        )
        self.quote = Quote(
            code="601288.SH",
            name="农业银行",
            price=Decimal("10"),
            previous_close=Decimal("10.1"),
            quote_time=datetime(2026, 8, 14, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

    def test_ttm_window_is_lower_exclusive_and_upper_inclusive(self):
        result = calculate_stock_snapshot(
            self.stock,
            self.quote,
            [
                action(date(2025, 8, 14), cash="10"),
                action(date(2025, 8, 15), cash="2"),
                action(date(2026, 8, 14), cash="3"),
                action(date(2026, 8, 15), cash="50"),
                action(date(2026, 1, 1), cash="50", status="预案"),
            ],
            date(2026, 8, 14),
        )

        self.assertEqual(result["ttm_dividend_per_share_pre_tax"], 0.5)
        self.assertEqual(result["dividend_yield_pct"], 5.0)
        self.assertEqual(result["recommendation"], "buy_candidate")
        self.assertEqual(len(result["dividends"]), 2)

    def test_cash_is_adjusted_for_same_and_later_bonus_shares(self):
        result = calculate_stock_snapshot(
            self.stock,
            self.quote,
            [
                action(date(2025, 9, 1), cash="10", bonus="10"),
                action(date(2026, 1, 1), transfer="10"),
            ],
            date(2026, 8, 14),
        )

        self.assertEqual(result["ttm_dividend_per_share_pre_tax"], 0.25)
        self.assertEqual(result["dividends"][0]["share_adjustment_factor"], 4.0)
        self.assertEqual(result["dividend_yield_pct"], 2.5)

    def test_zero_dividend_has_no_target_price(self):
        result = calculate_stock_snapshot(
            self.stock, self.quote, [], date(2026, 8, 14)
        )

        self.assertEqual(result["dividend_yield_pct"], 0.0)
        self.assertIsNone(result["target_price"])
        self.assertEqual(result["recommendation"], "watch")

    def test_sell_candidate_at_exact_sell_threshold(self):
        stock = StockConfig(
            "601288.SH",
            "农业银行",
            (
                PositionLevel(Decimal("5"), Decimal("25")),
                PositionLevel(Decimal("5.5"), Decimal("60")),
                PositionLevel(Decimal("6"), Decimal("100")),
            ),
            (
                PositionLevel(Decimal("4"), Decimal("70")),
                PositionLevel(Decimal("3.5"), Decimal("35")),
                PositionLevel(Decimal("3"), Decimal("0")),
            ),
        )
        result = calculate_stock_snapshot(
            stock,
            self.quote,
            [action(date(2026, 5, 13), cash="3")],
            date(2026, 8, 14),
        )

        self.assertEqual(result["dividend_yield_pct"], 3.0)
        self.assertEqual(result["recommendation"], "sell_candidate")
        self.assertEqual(result["signal_level"], 3)
        self.assertEqual(result["signal_target_position_pct"], 0.0)
        self.assertEqual(result["signal_target_price"], 10.0)

    def test_yield_between_buy_and_sell_thresholds_is_watch(self):
        stock = StockConfig(
            "601288.SH",
            "农业银行",
            (
                PositionLevel(Decimal("5"), Decimal("25")),
                PositionLevel(Decimal("5.5"), Decimal("60")),
                PositionLevel(Decimal("6"), Decimal("100")),
            ),
            (
                PositionLevel(Decimal("4"), Decimal("70")),
                PositionLevel(Decimal("3.5"), Decimal("35")),
                PositionLevel(Decimal("3"), Decimal("0")),
            ),
        )
        result = calculate_stock_snapshot(
            stock,
            self.quote,
            [action(date(2026, 5, 13), cash="4")],
            date(2026, 8, 14),
        )

        self.assertEqual(result["dividend_yield_pct"], 4.0)
        self.assertEqual(result["recommendation"], "sell_candidate")
        self.assertEqual(result["signal_level"], 1)

    def test_yield_in_neutral_gap_is_watch(self):
        stock = StockConfig(
            "601288.SH",
            "农业银行",
            (PositionLevel(Decimal("5"), Decimal("25")),),
            (PositionLevel(Decimal("4"), Decimal("70")),),
        )
        result = calculate_stock_snapshot(
            stock,
            self.quote,
            [action(date(2026, 5, 13), cash="4.5")],
            date(2026, 8, 14),
        )

        self.assertEqual(result["dividend_yield_pct"], 4.5)
        self.assertEqual(result["recommendation"], "watch")

    def test_highest_buy_level_wins(self):
        stock = StockConfig(
            "601288.SH",
            "农业银行",
            (
                PositionLevel(Decimal("5"), Decimal("25")),
                PositionLevel(Decimal("5.5"), Decimal("60")),
                PositionLevel(Decimal("6"), Decimal("100")),
            ),
            (),
        )
        result = calculate_stock_snapshot(
            stock,
            self.quote,
            [action(date(2026, 5, 13), cash="6")],
            date(2026, 8, 14),
        )

        self.assertEqual(result["signal_key"], "buy_3")
        self.assertEqual(result["signal_target_position_pct"], 100.0)

    def test_leap_day_calendar_year_subtraction(self):
        self.assertEqual(subtract_calendar_year(date(2024, 2, 29)), date(2023, 2, 28))


if __name__ == "__main__":
    unittest.main()
