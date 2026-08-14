from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


STOCK_CODE_PATTERN = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")


class ConfigError(ValueError):
    """Raised when the application configuration is invalid."""


@dataclass(frozen=True)
class PositionLevel:
    yield_threshold_pct: Decimal
    target_position_pct: Decimal


@dataclass(frozen=True)
class StockConfig:
    code: str
    name: str
    buy_levels: tuple[PositionLevel, ...]
    sell_levels: tuple[PositionLevel, ...]

    @property
    def yield_threshold_pct(self) -> Decimal:
        """Backward-compatible alias for the original buy threshold field."""
        return self.buy_levels[0].yield_threshold_pct

    @property
    def buy_yield_threshold_pct(self) -> Decimal:
        return self.buy_levels[0].yield_threshold_pct

    @property
    def sell_yield_threshold_pct(self) -> Decimal | None:
        return self.sell_levels[-1].yield_threshold_pct if self.sell_levels else None


@dataclass(frozen=True)
class NotifierConfig:
    notifier_type: str
    address_env: str
    auth_code_env: str


@dataclass(frozen=True)
class AppConfig:
    timezone_name: str
    stocks: tuple[StockConfig, ...]
    notifier: NotifierConfig

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件 {config_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件不是有效 JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("配置根节点必须是 JSON 对象")

    timezone_name = raw.get("timezone", "Asia/Shanghai")
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ConfigError("timezone 必须是非空字符串")
    timezone_name = timezone_name.strip()
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"未知时区: {timezone_name}") from exc

    stocks_raw = raw.get("stocks")
    if not isinstance(stocks_raw, list) or not stocks_raw:
        raise ConfigError("stocks 必须是非空数组")

    stocks: list[StockConfig] = []
    seen_codes: set[str] = set()
    for index, item in enumerate(stocks_raw):
        if not isinstance(item, dict):
            raise ConfigError(f"stocks[{index}] 必须是对象")

        code = str(item.get("code", "")).strip().upper()
        name = str(item.get("name", "")).strip()
        if not STOCK_CODE_PATTERN.fullmatch(code):
            raise ConfigError(
                f"stocks[{index}].code 必须是六位代码加 .SH/.SZ/.BJ: {code!r}"
            )
        if code in seen_codes:
            raise ConfigError(f"股票代码重复: {code}")
        if not name:
            raise ConfigError(f"stocks[{index}].name 不能为空")

        buy_levels, sell_levels = _load_position_levels(item, index)

        seen_codes.add(code)
        stocks.append(
            StockConfig(
                code=code,
                name=name,
                buy_levels=buy_levels,
                sell_levels=sell_levels,
            )
        )

    notifier_raw = raw.get("notifier")
    if not isinstance(notifier_raw, dict):
        raise ConfigError("notifier 必须是对象")
    notifier_type = str(notifier_raw.get("type", "")).strip().lower()
    if notifier_type != "qqmail":
        raise ConfigError("首版 notifier.type 只支持 qqmail")

    address_env = str(
        notifier_raw.get("address_env", "QQ_EMAIL_ADDRESS")
    ).strip()
    auth_code_env = str(
        notifier_raw.get("auth_code_env", "QQ_EMAIL_AUTH_CODE")
    ).strip()
    if not address_env or not auth_code_env:
        raise ConfigError("QQ 邮箱环境变量名不能为空")

    return AppConfig(
        timezone_name=timezone_name,
        stocks=tuple(stocks),
        notifier=NotifierConfig(
            notifier_type=notifier_type,
            address_env=address_env,
            auth_code_env=auth_code_env,
        ),
    )


def _load_position_levels(
    item: dict,
    stock_index: int,
) -> tuple[tuple[PositionLevel, ...], tuple[PositionLevel, ...]]:
    if "buy_levels" in item or "sell_levels" in item:
        buy_levels = _parse_levels(
            item.get("buy_levels"), stock_index, "buy_levels", expected_count=3
        )
        sell_levels = _parse_levels(
            item.get("sell_levels"), stock_index, "sell_levels", expected_count=3
        )
        _validate_ladder(buy_levels, sell_levels, stock_index)
        return buy_levels, sell_levels

    buy_threshold = _parse_percentage(
        item.get("buy_yield_threshold_pct", item.get("yield_threshold_pct")),
        f"stocks[{stock_index}].buy_yield_threshold_pct",
        allow_zero=False,
    )
    sell_raw = item.get("sell_yield_threshold_pct")
    sell_levels: tuple[PositionLevel, ...] = ()
    if sell_raw is not None:
        sell_threshold = _parse_percentage(
            sell_raw,
            f"stocks[{stock_index}].sell_yield_threshold_pct",
            allow_zero=False,
        )
        if sell_threshold >= buy_threshold:
            raise ConfigError(
                f"stocks[{stock_index}].sell_yield_threshold_pct 必须严格小于买入阈值"
            )
        sell_levels = (PositionLevel(sell_threshold, Decimal("0")),)
    return (PositionLevel(buy_threshold, Decimal("100")),), sell_levels


def _parse_levels(
    raw_levels: object,
    stock_index: int,
    field_name: str,
    *,
    expected_count: int,
) -> tuple[PositionLevel, ...]:
    field_path = f"stocks[{stock_index}].{field_name}"
    if not isinstance(raw_levels, list) or len(raw_levels) != expected_count:
        raise ConfigError(f"{field_path} 必须恰好包含 {expected_count} 档")

    levels: list[PositionLevel] = []
    for level_index, raw_level in enumerate(raw_levels):
        level_path = f"{field_path}[{level_index}]"
        if not isinstance(raw_level, dict):
            raise ConfigError(f"{level_path} 必须是对象")
        threshold = _parse_percentage(
            raw_level.get("yield_threshold_pct"),
            f"{level_path}.yield_threshold_pct",
            allow_zero=False,
        )
        target = _parse_percentage(
            raw_level.get("target_position_pct"),
            f"{level_path}.target_position_pct",
            allow_zero=True,
        )
        levels.append(PositionLevel(threshold, target))
    return tuple(levels)


def _parse_percentage(value: object, field_path: str, *, allow_zero: bool) -> Decimal:
    try:
        percentage = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ConfigError(f"{field_path} 必须是数字") from exc
    lower_invalid = percentage < 0 if allow_zero else percentage <= 0
    if not percentage.is_finite() or lower_invalid or percentage > 100:
        interval = "[0, 100]" if allow_zero else "(0, 100]"
        raise ConfigError(f"{field_path} 必须在 {interval} 范围内")
    return percentage


def _validate_ladder(
    buy_levels: tuple[PositionLevel, ...],
    sell_levels: tuple[PositionLevel, ...],
    stock_index: int,
) -> None:
    buy_thresholds = [level.yield_threshold_pct for level in buy_levels]
    buy_targets = [level.target_position_pct for level in buy_levels]
    sell_thresholds = [level.yield_threshold_pct for level in sell_levels]
    sell_targets = [level.target_position_pct for level in sell_levels]

    if not _strictly_increasing(buy_thresholds):
        raise ConfigError(f"stocks[{stock_index}].buy_levels 股息率必须逐档严格升高")
    if not _strictly_increasing(buy_targets) or buy_targets[0] <= 0:
        raise ConfigError(f"stocks[{stock_index}].buy_levels 目标仓位必须从大于 0 逐档升高")
    if not _strictly_decreasing(sell_thresholds):
        raise ConfigError(f"stocks[{stock_index}].sell_levels 股息率必须逐档严格降低")
    if not _strictly_decreasing(sell_targets):
        raise ConfigError(f"stocks[{stock_index}].sell_levels 目标仓位必须逐档降低")
    if sell_thresholds[0] >= buy_thresholds[0]:
        raise ConfigError(
            f"stocks[{stock_index}] 最高卖出线必须严格低于最低买入线"
        )


def _strictly_increasing(values: list[Decimal]) -> bool:
    return all(left < right for left, right in zip(values, values[1:]))


def _strictly_decreasing(values: list[Decimal]) -> bool:
    return all(left > right for left, right in zip(values, values[1:]))
