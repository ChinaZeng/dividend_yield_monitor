import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from config_loader import ConfigError, load_config


class ConfigLoaderTest(unittest.TestCase):
    def _write_config(self, root, stocks):
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

    def test_loads_valid_config_and_normalizes_code(self):
        with tempfile.TemporaryDirectory() as root:
            path = self._write_config(
                root,
                [{"code": "601288.sh", "name": "农业银行", "yield_threshold_pct": 5}],
            )
            config = load_config(path)

        self.assertEqual(config.timezone_name, "Asia/Shanghai")
        self.assertEqual(config.stocks[0].code, "601288.SH")
        self.assertEqual(config.stocks[0].yield_threshold_pct, Decimal("5"))

    def test_loads_buy_and_sell_thresholds(self):
        with tempfile.TemporaryDirectory() as root:
            path = self._write_config(
                root,
                [
                    {
                        "code": "601288.SH",
                        "name": "农业银行",
                        "buy_yield_threshold_pct": 5.5,
                        "sell_yield_threshold_pct": 3.3,
                    }
                ],
            )
            config = load_config(path)

        self.assertEqual(config.stocks[0].buy_yield_threshold_pct, Decimal("5.5"))
        self.assertEqual(config.stocks[0].sell_yield_threshold_pct, Decimal("3.3"))

    def test_loads_three_level_position_ladder(self):
        with tempfile.TemporaryDirectory() as root:
            path = self._write_config(
                root,
                [
                    {
                        "code": "601288.SH",
                        "name": "农业银行",
                        "buy_levels": [
                            {"yield_threshold_pct": 5, "target_position_pct": 25},
                            {"yield_threshold_pct": 5.5, "target_position_pct": 60},
                            {"yield_threshold_pct": 6, "target_position_pct": 100},
                        ],
                        "sell_levels": [
                            {"yield_threshold_pct": 3.9, "target_position_pct": 70},
                            {"yield_threshold_pct": 3.6, "target_position_pct": 35},
                            {"yield_threshold_pct": 3.3, "target_position_pct": 0},
                        ],
                    }
                ],
            )
            config = load_config(path)

        self.assertEqual(len(config.stocks[0].buy_levels), 3)
        self.assertEqual(config.stocks[0].buy_levels[1].target_position_pct, Decimal("60"))
        self.assertEqual(config.stocks[0].sell_levels[2].target_position_pct, Decimal("0"))

    def test_rejects_misordered_position_ladder(self):
        with tempfile.TemporaryDirectory() as root:
            path = self._write_config(
                root,
                [
                    {
                        "code": "601288.SH",
                        "name": "农业银行",
                        "buy_levels": [
                            {"yield_threshold_pct": 5.5, "target_position_pct": 25},
                            {"yield_threshold_pct": 5.0, "target_position_pct": 60},
                            {"yield_threshold_pct": 6.0, "target_position_pct": 100},
                        ],
                        "sell_levels": [
                            {"yield_threshold_pct": 3.9, "target_position_pct": 70},
                            {"yield_threshold_pct": 3.6, "target_position_pct": 35},
                            {"yield_threshold_pct": 3.3, "target_position_pct": 0},
                        ],
                    }
                ],
            )
            with self.assertRaisesRegex(ConfigError, "股息率必须逐档严格升高"):
                load_config(path)

    def test_rejects_sell_threshold_not_below_buy_threshold(self):
        for sell_threshold in (5.5, 6, 0):
            with self.subTest(sell_threshold=sell_threshold), tempfile.TemporaryDirectory() as root:
                path = self._write_config(
                    root,
                    [
                        {
                            "code": "601288.SH",
                            "name": "农业银行",
                            "buy_yield_threshold_pct": 5.5,
                            "sell_yield_threshold_pct": sell_threshold,
                        }
                    ],
                )
                with self.assertRaises(ConfigError):
                    load_config(path)

    def test_rejects_duplicate_codes(self):
        with tempfile.TemporaryDirectory() as root:
            path = self._write_config(
                root,
                [
                    {"code": "601288.SH", "name": "农业银行", "yield_threshold_pct": 5},
                    {"code": "601288.SH", "name": "重复", "yield_threshold_pct": 6},
                ],
            )
            with self.assertRaisesRegex(ConfigError, "股票代码重复"):
                load_config(path)

    def test_rejects_bad_code_and_threshold(self):
        cases = [
            [{"code": "601288", "name": "农业银行", "yield_threshold_pct": 5}],
            [{"code": "601288.SH", "name": "农业银行", "yield_threshold_pct": 0}],
            [{"code": "601288.SH", "name": "农业银行", "yield_threshold_pct": 101}],
        ]
        for stocks in cases:
            with self.subTest(stocks=stocks), tempfile.TemporaryDirectory() as root:
                path = self._write_config(root, stocks)
                with self.assertRaises(ConfigError):
                    load_config(path)


if __name__ == "__main__":
    unittest.main()
