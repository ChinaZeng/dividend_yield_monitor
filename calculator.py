from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from config_loader import PositionLevel, StockConfig
from data_sources import DividendAction, Quote


IMPLEMENTED_STATUS = "实施分配"


def subtract_calendar_year(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def calculate_stock_snapshot(
    stock: StockConfig,
    quote: Quote,
    actions: list[DividendAction],
    trade_date: date,
) -> dict:
    if quote.price <= 0:
        raise ValueError(f"{stock.code} 收盘价必须大于 0")

    window_start = subtract_calendar_year(trade_date)
    implemented = [
        action
        for action in actions
        if action.status == IMPLEMENTED_STATUS
        and action.ex_date is not None
        and action.ex_date <= trade_date
    ]
    cash_actions = [
        action
        for action in implemented
        if action.ex_date is not None
        and window_start < action.ex_date <= trade_date
        and action.cash_per_10_shares > 0
    ]

    included_dividends: list[dict] = []
    ttm_dividend = Decimal("0")
    for cash_action in cash_actions:
        adjustment_factor = Decimal("1")
        for later_action in implemented:
            if (
                later_action.ex_date is not None
                and cash_action.ex_date is not None
                and cash_action.ex_date <= later_action.ex_date <= trade_date
            ):
                added_shares = (
                    later_action.bonus_shares_per_10
                    + later_action.transfer_shares_per_10
                )
                if added_shares:
                    adjustment_factor *= Decimal("1") + added_shares / Decimal("10")

        raw_cash_per_share = cash_action.cash_per_10_shares / Decimal("10")
        adjusted_cash_per_share = raw_cash_per_share / adjustment_factor
        ttm_dividend += adjusted_cash_per_share
        included_dividends.append(
            {
                "report_period": cash_action.report_period,
                "ex_date": cash_action.ex_date.isoformat(),
                "description": cash_action.description,
                "cash_per_10_shares_pre_tax": _json_number(
                    cash_action.cash_per_10_shares, 6
                ),
                "cash_per_share_pre_tax_raw": _json_number(
                    raw_cash_per_share, 8
                ),
                "share_adjustment_factor": _json_number(adjustment_factor, 8),
                "cash_per_share_pre_tax_adjusted": _json_number(
                    adjusted_cash_per_share, 8
                ),
            }
        )

    yield_pct = ttm_dividend / quote.price * Decimal("100")
    buy_levels = _serialize_levels(stock.buy_levels, ttm_dividend)
    sell_levels = _serialize_levels(stock.sell_levels, ttm_dividend)
    signal_side, signal_level, matched_level = _match_position_level(stock, yield_pct)
    if signal_side == "buy":
        recommendation = "buy_candidate"
    elif signal_side == "sell":
        recommendation = "sell_candidate"
    else:
        recommendation = "watch"

    entry_buy = stock.buy_levels[0]
    terminal_sell = stock.sell_levels[-1] if stock.sell_levels else None
    entry_buy_price = _target_price(ttm_dividend, entry_buy.yield_threshold_pct)
    terminal_sell_price = (
        _target_price(ttm_dividend, terminal_sell.yield_threshold_pct)
        if terminal_sell
        else None
    )
    entry_sell = stock.sell_levels[0] if stock.sell_levels else None

    return {
        "code": stock.code,
        "name": stock.name,
        "source_name": quote.name,
        "quote_time": quote.quote_time.isoformat(),
        "close": _json_number(quote.price, 4),
        "previous_close": _json_number(quote.previous_close, 4),
        "ttm_window": {
            "start_exclusive": window_start.isoformat(),
            "end_inclusive": trade_date.isoformat(),
        },
        "dividends": included_dividends,
        "ttm_dividend_per_share_pre_tax": _json_number(ttm_dividend, 8),
        "dividend_yield_pct": _json_number(yield_pct, 6),
        "buy_levels": buy_levels,
        "sell_levels": sell_levels,
        "signal_side": signal_side,
        "signal_level": signal_level,
        "signal_key": f"{signal_side}_{signal_level}" if signal_level else "watch",
        "signal_threshold_pct": (
            _json_number(matched_level.yield_threshold_pct, 6)
            if matched_level
            else None
        ),
        "signal_target_position_pct": (
            _json_number(matched_level.target_position_pct, 2)
            if matched_level
            else None
        ),
        "signal_target_price": (
            _json_number(
                _target_price(ttm_dividend, matched_level.yield_threshold_pct), 4
            )
            if matched_level and ttm_dividend > 0
            else None
        ),
        "model_position_pct": None,
        "position_action": None,
        "signal_event": None,
        "previous_signal_key": None,
        "buy_yield_threshold_pct": _json_number(entry_buy.yield_threshold_pct, 6),
        "sell_yield_threshold_pct": (
            _json_number(terminal_sell.yield_threshold_pct, 6)
            if terminal_sell
            else None
        ),
        "buy_target_price": _json_number(entry_buy_price, 4),
        "sell_target_price": _json_number(terminal_sell_price, 4),
        "buy_yield_gap_pp": _json_number(
            yield_pct - entry_buy.yield_threshold_pct, 6
        ),
        "sell_yield_gap_pp": (
            _json_number(yield_pct - entry_sell.yield_threshold_pct, 6)
            if entry_sell
            else None
        ),
        "yield_threshold_pct": _json_number(entry_buy.yield_threshold_pct, 6),
        "target_price": _json_number(entry_buy_price, 4),
        "yield_gap_pp": _json_number(yield_pct - entry_buy.yield_threshold_pct, 6),
        "recommendation": recommendation,
        "error": None,
    }


def build_error_stock_snapshot(
    stock: StockConfig,
    error: str,
    quote: Quote | None = None,
) -> dict:
    return {
        "code": stock.code,
        "name": stock.name,
        "source_name": quote.name if quote else None,
        "quote_time": quote.quote_time.isoformat() if quote else None,
        "close": _json_number(quote.price, 4) if quote else None,
        "previous_close": _json_number(quote.previous_close, 4) if quote else None,
        "ttm_window": None,
        "dividends": [],
        "ttm_dividend_per_share_pre_tax": None,
        "dividend_yield_pct": None,
        "buy_levels": _serialize_levels(stock.buy_levels, None),
        "sell_levels": _serialize_levels(stock.sell_levels, None),
        "signal_side": "error",
        "signal_level": None,
        "signal_key": "data_error",
        "signal_threshold_pct": None,
        "signal_target_position_pct": None,
        "signal_target_price": None,
        "model_position_pct": None,
        "position_action": None,
        "signal_event": None,
        "previous_signal_key": None,
        "buy_yield_threshold_pct": _json_number(stock.buy_yield_threshold_pct, 6),
        "sell_yield_threshold_pct": _json_number(stock.sell_yield_threshold_pct, 6),
        "buy_target_price": None,
        "sell_target_price": None,
        "buy_yield_gap_pp": None,
        "sell_yield_gap_pp": None,
        "yield_threshold_pct": _json_number(stock.buy_yield_threshold_pct, 6),
        "target_price": None,
        "yield_gap_pp": None,
        "recommendation": "data_error",
        "error": error,
    }


def _json_number(value: Decimal | None, places: int) -> float | None:
    if value is None:
        return None
    quantizer = Decimal("1").scaleb(-places)
    return float(value.quantize(quantizer, rounding=ROUND_HALF_UP))


def _match_position_level(
    stock: StockConfig,
    yield_pct: Decimal,
) -> tuple[str, int | None, PositionLevel | None]:
    matched_buy: tuple[int, PositionLevel] | None = None
    for index, level in enumerate(stock.buy_levels, start=1):
        if yield_pct >= level.yield_threshold_pct:
            matched_buy = (index, level)

    if matched_buy:
        return "buy", matched_buy[0], matched_buy[1]

    matched_sell: tuple[int, PositionLevel] | None = None
    for index, level in enumerate(stock.sell_levels, start=1):
        if yield_pct <= level.yield_threshold_pct:
            matched_sell = (index, level)
    if matched_sell:
        return "sell", matched_sell[0], matched_sell[1]
    return "watch", None, None


def _serialize_levels(
    levels: tuple[PositionLevel, ...],
    ttm_dividend: Decimal | None,
) -> list[dict]:
    serialized: list[dict] = []
    for index, level in enumerate(levels, start=1):
        price = (
            _target_price(ttm_dividend, level.yield_threshold_pct)
            if ttm_dividend is not None and ttm_dividend > 0
            else None
        )
        serialized.append(
            {
                "level": index,
                "yield_threshold_pct": _json_number(level.yield_threshold_pct, 6),
                "target_position_pct": _json_number(level.target_position_pct, 2),
                "target_price": _json_number(price, 4),
            }
        )
    return serialized


def _target_price(ttm_dividend: Decimal, threshold: Decimal) -> Decimal | None:
    if ttm_dividend <= 0:
        return None
    return ttm_dividend / (threshold / Decimal("100"))
