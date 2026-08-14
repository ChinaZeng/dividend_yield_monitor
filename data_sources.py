from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

import requests


TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={symbols}"
TENCENT_REFERER = "https://gu.qq.com/"
MARKET_PROBE_CODE = "000001.SH"


class DataSourceError(RuntimeError):
    """Raised when an upstream market-data source cannot be trusted."""


@dataclass(frozen=True)
class Quote:
    code: str
    name: str
    price: Decimal
    previous_close: Decimal
    quote_time: datetime


@dataclass(frozen=True)
class DividendAction:
    report_period: str | None
    ex_date: date | None
    status: str
    cash_per_10_shares: Decimal
    bonus_shares_per_10: Decimal
    transfer_shares_per_10: Decimal
    description: str | None = None


class TencentQuoteClient:
    def __init__(
        self,
        timezone: ZoneInfo,
        session: requests.Session | None = None,
        max_attempts: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timezone = timezone
        self.session = session or requests.Session()
        self.max_attempts = max_attempts
        self.sleeper = sleeper

    def fetch_quotes(self, codes: Iterable[str]) -> dict[str, Quote]:
        requested_codes = list(dict.fromkeys(code.upper() for code in codes))
        if not requested_codes:
            return {}

        symbol_by_code = {code: to_tencent_symbol(code) for code in requested_codes}
        url = TENCENT_QUOTE_URL.format(symbols=",".join(symbol_by_code.values()))

        def request_once() -> str:
            response = self.session.get(
                url,
                headers={
                    "Referer": TENCENT_REFERER,
                    "User-Agent": "Mozilla/5.0 dividend-yield-monitor/1.0",
                },
                timeout=(5, 15),
            )
            response.raise_for_status()
            return response.content.decode("gb18030")

        text = _with_retries(
            request_once,
            attempts=self.max_attempts,
            sleeper=self.sleeper,
            label="腾讯行情请求",
        )
        return parse_tencent_quotes(text, symbol_by_code, self.timezone)


class AkshareDividendClient:
    def __init__(
        self,
        ak_module: Any | None = None,
        max_attempts: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._ak_module = ak_module
        self.max_attempts = max_attempts
        self.sleeper = sleeper

    @property
    def ak(self) -> Any:
        if self._ak_module is None:
            try:
                import akshare as ak
            except ImportError as exc:
                raise DataSourceError(
                    "未安装 AKShare，请先执行 pip install -r requirements.txt"
                ) from exc
            self._ak_module = ak
        return self._ak_module

    def fetch_actions(self, code: str) -> list[DividendAction]:
        symbol = code.split(".", 1)[0]

        def fetch_once() -> Any:
            return self.ak.stock_fhps_detail_em(symbol=symbol)

        frame = _with_retries(
            fetch_once,
            attempts=self.max_attempts,
            sleeper=self.sleeper,
            label=f"{code} 分红数据请求",
        )
        return parse_dividend_frame(frame, code)


def to_tencent_symbol(code: str) -> str:
    ticker, exchange = code.upper().split(".", 1)
    if exchange not in {"SH", "SZ", "BJ"}:
        raise ValueError(f"不支持的交易所代码: {code}")
    return f"{exchange.lower()}{ticker}"


def parse_tencent_quotes(
    text: str,
    symbol_by_code: dict[str, str],
    timezone: ZoneInfo,
) -> dict[str, Quote]:
    payload_by_symbol = {
        match.group(1).lower(): match.group(2)
        for match in re.finditer(r'v_([A-Za-z0-9]+)="([^"]*)";', text)
    }
    quotes: dict[str, Quote] = {}
    for code, symbol in symbol_by_code.items():
        payload = payload_by_symbol.get(symbol.lower())
        if not payload:
            continue
        fields = payload.split("~")
        if len(fields) <= 30 or fields[0] != "1":
            continue
        try:
            price = Decimal(fields[3])
            previous_close = Decimal(fields[4])
            quote_time = datetime.strptime(fields[30], "%Y%m%d%H%M%S").replace(
                tzinfo=timezone
            )
        except (InvalidOperation, ValueError, IndexError) as exc:
            raise DataSourceError(f"腾讯行情 {code} 字段格式异常") from exc
        if not price.is_finite() or not previous_close.is_finite():
            raise DataSourceError(f"腾讯行情 {code} 包含非有限数值")
        quotes[code] = Quote(
            code=code,
            name=fields[1].strip(),
            price=price,
            previous_close=previous_close,
            quote_time=quote_time,
        )
    return quotes


def parse_dividend_frame(frame: Any, code: str) -> list[DividendAction]:
    if frame is None:
        raise DataSourceError(f"{code} 分红数据为空对象")
    if bool(getattr(frame, "empty", False)):
        return []

    required_columns = {
        "报告期",
        "现金分红-现金分红比例",
        "现金分红-现金分红比例描述",
        "送转股份-送股比例",
        "送转股份-转股比例",
        "除权除息日",
        "方案进度",
    }
    columns = set(getattr(frame, "columns", []))
    missing = sorted(required_columns - columns)
    if missing:
        raise DataSourceError(f"{code} 分红数据缺少字段: {', '.join(missing)}")

    actions: list[DividendAction] = []
    seen: set[tuple[Any, ...]] = set()
    for _, row in frame.iterrows():
        status = _optional_text(row["方案进度"]) or ""
        ex_date = _optional_date(row["除权除息日"])
        if status == "实施分配" and ex_date is None:
            raise DataSourceError(f"{code} 已实施分红记录缺少除权除息日")

        action = DividendAction(
            report_period=_optional_date_text(row["报告期"]),
            ex_date=ex_date,
            status=status,
            cash_per_10_shares=_optional_decimal(
                row["现金分红-现金分红比例"], code
            ),
            bonus_shares_per_10=_optional_decimal(
                row["送转股份-送股比例"], code
            ),
            transfer_shares_per_10=_optional_decimal(
                row["送转股份-转股比例"], code
            ),
            description=_optional_text(row["现金分红-现金分红比例描述"]),
        )
        key = (
            action.report_period,
            action.ex_date,
            action.status,
            action.cash_per_10_shares,
            action.bonus_shares_per_10,
            action.transfer_shares_per_10,
        )
        if key not in seen:
            seen.add(key)
            actions.append(action)

    return sorted(actions, key=lambda item: item.ex_date or date.min)


def _with_retries(
    call: Callable[[], Any],
    attempts: int,
    sleeper: Callable[[float], None],
    label: str = "数据请求",
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as exc:  # upstream libraries raise several exception types
            last_error = exc
            if attempt < attempts:
                sleeper(float(2 ** (attempt - 1)))
    raise DataSourceError(f"{label}连续 {attempts} 次失败: {last_error}") from last_error


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    text = str(value).strip()
    return not text or text.lower() in {"nan", "nat", "none"}


def _optional_text(value: Any) -> str | None:
    return None if _is_missing(value) else str(value).strip()


def _optional_date_text(value: Any) -> str | None:
    parsed = _optional_date(value)
    return parsed.isoformat() if parsed else None


def _optional_date(value: Any) -> date | None:
    if _is_missing(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise DataSourceError(f"无法解析日期: {value!r}") from exc


def _optional_decimal(value: Any, code: str) -> Decimal:
    if _is_missing(value):
        return Decimal("0")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise DataSourceError(f"{code} 分红数据包含无效数字: {value!r}") from exc
    if not number.is_finite() or number < 0:
        raise DataSourceError(f"{code} 分红数据包含非法数字: {value!r}")
    return number
