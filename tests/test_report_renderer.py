import tempfile
import unittest
from pathlib import Path

from PIL import Image

from report_renderer import DISCLAIMER, build_markdown, render_report_image


def sample_snapshot():
    return {
        "schema_version": 1,
        "status": "partial",
        "trade_date": "2026-08-14",
        "report_date": "2026-08-14",
        "generated_at": "2026-08-14T16:30:00+08:00",
        "sources": {"quotes": "腾讯行情", "dividends": "东方财富/AKShare"},
        "stocks": [
            {
                "code": "601288.SH",
                "name": "农业银行",
                "close": 4.8,
                "ttm_dividend_per_share_pre_tax": 0.25,
                "dividend_yield_pct": 5.208333,
                "buy_levels": [
                    {"level": 1, "yield_threshold_pct": 5.0, "target_position_pct": 25, "target_price": 5.0},
                    {"level": 2, "yield_threshold_pct": 5.5, "target_position_pct": 60, "target_price": 4.5455},
                    {"level": 3, "yield_threshold_pct": 6.0, "target_position_pct": 100, "target_price": 4.1667},
                ],
                "sell_levels": [
                    {"level": 1, "yield_threshold_pct": 3.9, "target_position_pct": 70, "target_price": 6.4103},
                    {"level": 2, "yield_threshold_pct": 3.6, "target_position_pct": 35, "target_price": 6.9444},
                    {"level": 3, "yield_threshold_pct": 3.3, "target_position_pct": 0, "target_price": 7.5758},
                ],
                "signal_side": "buy",
                "signal_level": 1,
                "signal_key": "buy_1",
                "signal_target_position_pct": 25,
                "model_position_pct": 25,
                "position_action": "establish",
                "signal_event": "initial",
                "buy_yield_threshold_pct": 5.0,
                "sell_yield_threshold_pct": 3.3,
                "buy_target_price": 5.0,
                "sell_target_price": 7.575758,
                "buy_yield_gap_pp": 0.208333,
                "sell_yield_gap_pp": 1.908333,
                "yield_threshold_pct": 5.0,
                "target_price": 5.0,
                "yield_gap_pp": 0.208333,
                "recommendation": "buy_candidate",
                "error": None,
            },
            {
                "code": "600941.SH",
                "name": "中国移动",
                "close": 120.61,
                "ttm_dividend_per_share_pre_tax": 4.7037,
                "dividend_yield_pct": 3.9,
                "buy_levels": [
                    {"level": 1, "yield_threshold_pct": 4.75, "target_position_pct": 25, "target_price": 99.03},
                    {"level": 2, "yield_threshold_pct": 5.0, "target_position_pct": 60, "target_price": 94.07},
                    {"level": 3, "yield_threshold_pct": 5.25, "target_position_pct": 100, "target_price": 89.59},
                ],
                "sell_levels": [
                    {"level": 1, "yield_threshold_pct": 4.18, "target_position_pct": 70, "target_price": 112.53},
                    {"level": 2, "yield_threshold_pct": 4.1, "target_position_pct": 35, "target_price": 114.72},
                    {"level": 3, "yield_threshold_pct": 4.0, "target_position_pct": 0, "target_price": 117.59},
                ],
                "signal_side": "sell",
                "signal_level": 3,
                "signal_key": "sell_3",
                "signal_target_position_pct": 0,
                "model_position_pct": 0,
                "position_action": "reduce",
                "signal_event": "crossed",
                "buy_yield_threshold_pct": 5.0,
                "sell_yield_threshold_pct": 4.0,
                "buy_target_price": 94.074,
                "sell_target_price": 117.5925,
                "buy_yield_gap_pp": -1.1,
                "sell_yield_gap_pp": -0.1,
                "recommendation": "sell_candidate",
                "error": None,
            },
            {
                "code": "000001.SZ",
                "name": "很长的测试股票名称",
                "close": None,
                "ttm_dividend_per_share_pre_tax": None,
                "dividend_yield_pct": None,
                "yield_threshold_pct": 4.0,
                "target_price": None,
                "yield_gap_pp": None,
                "recommendation": "data_error",
                "error": "行情日期陈旧，无法使用上一交易日价格",
            },
        ],
        "error": None,
    }


class ReportRendererTest(unittest.TestCase):
    def test_markdown_contains_decision_table_error_and_disclaimer(self):
        markdown = build_markdown(sample_snapshot())

        self.assertIn("农业银行 (601288.SH)", markdown)
        self.assertIn("5.21%", markdown)
        self.assertIn("买入 1 档 · 模型仓位 25% · 首次识别", markdown)
        self.assertIn("卖出 3 档 · 上限 0% / 模型 0% · 新跨档", markdown)
        self.assertIn("买入档股票：1", markdown)
        self.assertIn("卖出档股票：1", markdown)
        self.assertIn("1档  5.00% / ¥5.00 → 25%", markdown)
        self.assertIn("数据异常详情", markdown)
        self.assertIn(DISCLAIMER, markdown)

    def test_png_renders_with_chinese_font_and_expected_size(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "report.png"
            render_report_image(sample_snapshot(), output)
            with Image.open(output) as image:
                self.assertEqual(image.format, "PNG")
                self.assertGreaterEqual(image.width, 1600)
                self.assertGreater(image.height, 500)


if __name__ == "__main__":
    unittest.main()
