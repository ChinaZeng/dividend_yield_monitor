import unittest
from datetime import date
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from data_sources import (
    DataSourceError,
    TencentQuoteClient,
    parse_dividend_frame,
    parse_tencent_quotes,
    to_tencent_symbol,
)


def tencent_line(symbol, name, code, price, previous_close, timestamp):
    fields = [""] * 35
    fields[0] = "1"
    fields[1] = name
    fields[2] = code
    fields[3] = str(price)
    fields[4] = str(previous_close)
    fields[30] = timestamp
    return f'v_{symbol}="{"~".join(fields)}";'


class TencentQuoteTest(unittest.TestCase):
    def test_symbol_conversion(self):
        self.assertEqual(to_tencent_symbol("601288.SH"), "sh601288")
        self.assertEqual(to_tencent_symbol("000001.SZ"), "sz000001")
        self.assertEqual(to_tencent_symbol("920001.BJ"), "bj920001")

    def test_parses_batch_response(self):
        text = "\n".join(
            [
                tencent_line("sh000001", "上证指数", "000001", 3200, 3190, "20260814150100"),
                tencent_line("sh601288", "农业银行", "601288", 6.47, 6.56, "20260814161458"),
            ]
        )
        quotes = parse_tencent_quotes(
            text,
            {"000001.SH": "sh000001", "601288.SH": "sh601288"},
            ZoneInfo("Asia/Shanghai"),
        )

        self.assertEqual(str(quotes["601288.SH"].price), "6.47")
        self.assertEqual(quotes["601288.SH"].quote_time.date(), date(2026, 8, 14))
        self.assertEqual(quotes["601288.SH"].name, "农业银行")

    def test_client_retries_transient_request_failure(self):
        response = Mock()
        response.content = tencent_line(
            "sh601288", "农业银行", "601288", 6.47, 6.56, "20260814161458"
        ).encode("gb18030")
        response.raise_for_status.return_value = None
        session = Mock()
        session.get.side_effect = [requests.ConnectionError("temporary"), response]
        sleeper = Mock()
        client = TencentQuoteClient(
            ZoneInfo("Asia/Shanghai"),
            session=session,
            max_attempts=3,
            sleeper=sleeper,
        )

        quotes = client.fetch_quotes(["601288.SH"])

        self.assertIn("601288.SH", quotes)
        self.assertEqual(session.get.call_count, 2)
        sleeper.assert_called_once_with(1.0)

    def test_malformed_numeric_field_is_rejected(self):
        text = tencent_line(
            "sh601288", "农业银行", "601288", "bad", 6.56, "20260814161458"
        )
        with self.assertRaises(DataSourceError):
            parse_tencent_quotes(
                text,
                {"601288.SH": "sh601288"},
                ZoneInfo("Asia/Shanghai"),
            )


class DividendFrameTest(unittest.TestCase):
    def test_empty_frame_means_no_dividend_history(self):
        self.assertEqual(parse_dividend_frame(pd.DataFrame(), "601288.SH"), [])

    def test_parses_and_deduplicates_dividend_rows(self):
        row = {
            "报告期": "2025-12-31",
            "现金分红-现金分红比例": 1.3,
            "现金分红-现金分红比例描述": "10派1.30元(含税)",
            "送转股份-送股比例": None,
            "送转股份-转股比例": None,
            "除权除息日": "2026-05-13",
            "方案进度": "实施分配",
        }
        actions = parse_dividend_frame(pd.DataFrame([row, row]), "601288.SH")

        self.assertEqual(len(actions), 1)
        self.assertEqual(str(actions[0].cash_per_10_shares), "1.3")
        self.assertEqual(actions[0].ex_date, date(2026, 5, 13))

    def test_implemented_row_without_ex_date_is_rejected(self):
        frame = pd.DataFrame(
            [
                {
                    "报告期": "2025-12-31",
                    "现金分红-现金分红比例": 1.3,
                    "现金分红-现金分红比例描述": "10派1.30元(含税)",
                    "送转股份-送股比例": None,
                    "送转股份-转股比例": None,
                    "除权除息日": None,
                    "方案进度": "实施分配",
                }
            ]
        )
        with self.assertRaisesRegex(DataSourceError, "缺少除权除息日"):
            parse_dividend_frame(frame, "601288.SH")

    def test_missing_schema_field_is_rejected(self):
        with self.assertRaisesRegex(DataSourceError, "缺少字段"):
            parse_dividend_frame(pd.DataFrame([{"报告期": "2025-12-31"}]), "601288.SH")


if __name__ == "__main__":
    unittest.main()
