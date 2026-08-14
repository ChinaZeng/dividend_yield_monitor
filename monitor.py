#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, time
from pathlib import Path
from typing import Any

from calculator import build_error_stock_snapshot, calculate_stock_snapshot
from config_loader import AppConfig, ConfigError, load_config
from data_sources import (
    MARKET_PROBE_CODE,
    AkshareDividendClient,
    DataSourceError,
    TencentQuoteClient,
)
from notifier import NotificationError, build_notifier
from report_renderer import build_markdown, build_report_title, render_report_image


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / ".report"
DEFAULT_SNAPSHOT_DIR = PROJECT_ROOT / "data"
SNAPSHOT_SCHEMA_VERSION = 2
MANIFEST_SCHEMA_VERSION = 1


def prepare_run(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    snapshot_dir: str | Path = DEFAULT_SNAPSHOT_DIR,
    quote_client: TencentQuoteClient | None = None,
    dividend_client: AkshareDividendClient | None = None,
    now: datetime | None = None,
) -> Path:
    config = load_config(config_path)
    current_time = _normalize_now(now, config)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "manifest.json"

    quote_source = quote_client or TencentQuoteClient(config.timezone)
    dividend_source = dividend_client or AkshareDividendClient()
    requested_codes = [MARKET_PROBE_CODE, *(stock.code for stock in config.stocks)]

    try:
        quotes = quote_source.fetch_quotes(requested_codes)
        market_quote = quotes.get(MARKET_PROBE_CODE)
        if market_quote is None:
            raise DataSourceError("腾讯行情未返回上证指数市场探针")
        if market_quote.price <= 0:
            raise DataSourceError("上证指数市场探针价格无效")
    except Exception as exc:
        snapshot = _build_fatal_snapshot(
            config,
            current_time,
            f"市场探针失败：{exc}",
        )
        return _write_report_bundle(snapshot, output, manifest_path, snapshot_path=None)

    market_date = market_quote.quote_time.date()
    if market_date != current_time.date():
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "status": "skipped_non_trading_day",
            "generated_at": current_time.isoformat(),
            "trade_date": None,
            "market_last_quote_date": market_date.isoformat(),
            "reason": "市场探针不是当日行情，按非交易日跳过",
            "title": None,
            "snapshot_path": None,
            "markdown_path": None,
            "image_path": None,
        }
        _atomic_write_json(manifest_path, manifest)
        return manifest_path

    if market_quote.quote_time.timetz().replace(tzinfo=None) < time(15, 0):
        snapshot = _build_fatal_snapshot(
            config,
            current_time,
            f"市场尚未收盘：探针时间 {market_quote.quote_time.isoformat()}",
            trade_date=market_date,
        )
        return _write_report_bundle(snapshot, output, manifest_path, snapshot_path=None)

    previous_snapshot = _load_previous_snapshot(Path(snapshot_dir), market_date)
    stock_rows: list[dict[str, Any]] = []
    for stock in config.stocks:
        quote = quotes.get(stock.code)
        if quote is None:
            stock_rows.append(build_error_stock_snapshot(stock, "行情源未返回该股票"))
            continue
        if quote.quote_time.date() != market_date:
            stock_rows.append(
                build_error_stock_snapshot(
                    stock,
                    f"行情陈旧：最近报价日期 {quote.quote_time.date().isoformat()}",
                    quote,
                )
            )
            continue
        if quote.quote_time.timetz().replace(tzinfo=None) < time(15, 0):
            stock_rows.append(
                build_error_stock_snapshot(
                    stock,
                    f"收盘行情时间异常：{quote.quote_time.isoformat()}",
                    quote,
                )
            )
            continue
        if quote.price <= 0:
            stock_rows.append(build_error_stock_snapshot(stock, "收盘价无效", quote))
            continue

        try:
            actions = dividend_source.fetch_actions(stock.code)
            row = calculate_stock_snapshot(stock, quote, actions, market_date)
        except Exception as exc:
            row = build_error_stock_snapshot(stock, f"分红计算失败：{exc}", quote)
        stock_rows.append(row)

    _annotate_position_state(stock_rows, previous_snapshot)

    success_count = sum(row["recommendation"] != "data_error" for row in stock_rows)
    if success_count == len(stock_rows):
        status = "ok"
    elif success_count == 0:
        status = "failed"
    else:
        status = "partial"

    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "status": status,
        "trade_date": market_date.isoformat(),
        "report_date": market_date.isoformat(),
        "generated_at": current_time.isoformat(),
        "market_probe": {
            "code": MARKET_PROBE_CODE,
            "name": market_quote.name,
            "price": float(market_quote.price),
            "quote_time": market_quote.quote_time.isoformat(),
        },
        "sources": {
            "quotes": "腾讯行情 qt.gtimg.cn",
            "dividends": "东方财富分红配送详情（AKShare stock_fhps_detail_em）",
        },
        "stocks": stock_rows,
        "error": None,
    }

    snapshot_path = Path(snapshot_dir) / f"{market_date.isoformat()}.json"
    _atomic_write_json(snapshot_path, snapshot)
    return _write_report_bundle(snapshot, output, manifest_path, snapshot_path)


def send_prepared_report(
    manifest_path: str | Path,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
) -> None:
    manifest = _read_json(manifest_path)
    if manifest.get("status") == "skipped_non_trading_day":
        print("非交易日，无需发送邮件。")
        return
    markdown_path = _resolve_recorded_path(manifest.get("markdown_path"))
    image_path = _resolve_recorded_path(manifest.get("image_path"))
    if markdown_path is None or image_path is None:
        raise NotificationError("manifest 中没有可发送的报告文件")
    config = load_config(config_path)
    notifier = build_notifier(config)
    notifier.send(manifest["title"], markdown_path, image_path)
    print(f"QQ 邮件已发送至 {notifier.address}")


def check_prepared_run(manifest_path: str | Path) -> int:
    manifest = _read_json(manifest_path)
    status = manifest.get("status")
    if status in {"ok", "skipped_non_trading_day"}:
        print(f"运行状态: {status}")
        return 0
    print(f"运行状态: {status}; 数据存在异常", file=sys.stderr)
    return 1


def _build_fatal_snapshot(
    config: AppConfig,
    current_time: datetime,
    error: str,
    trade_date=None,
) -> dict[str, Any]:
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "status": "failed",
        "trade_date": trade_date.isoformat() if trade_date else None,
        "report_date": current_time.date().isoformat(),
        "generated_at": current_time.isoformat(),
        "market_probe": {"code": MARKET_PROBE_CODE, "error": error},
        "sources": {
            "quotes": "腾讯行情 qt.gtimg.cn",
            "dividends": "东方财富分红配送详情（AKShare stock_fhps_detail_em）",
        },
        "stocks": [
            build_error_stock_snapshot(stock, error) for stock in config.stocks
        ],
        "error": error,
    }


def _write_report_bundle(
    snapshot: dict[str, Any],
    output_dir: Path,
    manifest_path: Path,
    snapshot_path: Path | None,
) -> Path:
    report_date = snapshot.get("trade_date") or snapshot.get("report_date")
    base_name = f"dividend-yield-{report_date}"
    markdown_path = output_dir / f"{base_name}.md"
    image_path = output_dir / f"{base_name}.png"
    _atomic_write_text(markdown_path, build_markdown(snapshot))
    render_report_image(snapshot, image_path)

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "status": snapshot["status"],
        "generated_at": snapshot["generated_at"],
        "trade_date": snapshot.get("trade_date"),
        "title": build_report_title(snapshot),
        "snapshot_path": _record_path(snapshot_path),
        "markdown_path": _record_path(markdown_path),
        "image_path": _record_path(image_path),
        "error": snapshot.get("error"),
    }
    _atomic_write_json(manifest_path, manifest)
    return manifest_path


def _normalize_now(now: datetime | None, config: AppConfig) -> datetime:
    if now is None:
        return datetime.now(config.timezone)
    if now.tzinfo is None:
        return now.replace(tzinfo=config.timezone)
    return now.astimezone(config.timezone)


def _record_path(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def _resolve_recorded_path(value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _read_json(path: str | Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取 manifest {path}: {exc}") from exc


def _load_previous_snapshot(
    snapshot_dir: Path,
    trade_date,
) -> dict[str, Any] | None:
    if not snapshot_dir.exists():
        return None
    candidates: list[tuple[str, dict[str, Any]]] = []
    for path in snapshot_dir.glob("*.json"):
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        snapshot_date = snapshot.get("trade_date")
        if isinstance(snapshot_date, str) and snapshot_date <= trade_date.isoformat():
            candidates.append((snapshot_date, snapshot))
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _annotate_position_state(
    rows: list[dict[str, Any]],
    previous_snapshot: dict[str, Any] | None,
) -> None:
    previous_rows = {
        row.get("code"): row
        for row in (previous_snapshot or {}).get("stocks", [])
        if row.get("code")
    }
    previous_trade_date = (previous_snapshot or {}).get("trade_date")

    for row in rows:
        if row.get("recommendation") == "data_error":
            continue
        previous = previous_rows.get(row.get("code"))
        previous_key = previous.get("signal_key") if previous else None
        previous_position = _optional_float(
            previous.get("model_position_pct") if previous else None
        )
        side = row.get("signal_side")
        raw_target = _optional_float(row.get("signal_target_position_pct"))

        if side == "buy" and raw_target is not None:
            model_position = max(previous_position or 0.0, raw_target)
        elif side == "sell" and raw_target is not None:
            starting_position = 100.0 if previous_position is None else previous_position
            model_position = min(starting_position, raw_target)
        else:
            model_position = previous_position

        if previous_position is None:
            if side == "buy":
                action = "establish"
            elif side == "sell":
                action = "cap"
            else:
                action = "none"
        elif model_position is not None and model_position > previous_position:
            action = "increase"
        elif model_position is not None and model_position < previous_position:
            action = "reduce"
        else:
            action = "hold"

        current_key = row.get("signal_key")
        if previous_key is None:
            event = "initial" if current_key != "watch" else "none"
        elif current_key == previous_key:
            event = "maintain"
        else:
            event = "crossed"

        row["previous_trade_date"] = previous_trade_date
        row["previous_signal_key"] = previous_key
        row["previous_model_position_pct"] = previous_position
        row["model_position_pct"] = model_position
        row["position_action"] = action
        row["signal_event"] = event


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _atomic_write_json(path: str | Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    _atomic_write_text(path, text)


def _atomic_write_text(path: str | Path, text: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = handle.name
        os.replace(temporary_path, destination)
    finally:
        if temporary_path and Path(temporary_path).exists():
            Path(temporary_path).unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="A 股 TTM 股息率监控")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="配置文件路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="生成、发送并检查一次日报")
    run_parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))

    prepare_parser = subparsers.add_parser("prepare", help="生成快照和报告")
    prepare_parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))

    send_parser = subparsers.add_parser("send", help="发送已生成的报告")
    send_parser.add_argument("--manifest", required=True)

    check_parser = subparsers.add_parser("check", help="按 manifest 检查运行状态")
    check_parser.add_argument("--manifest", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            manifest_path = prepare_run(args.config, args.output_dir)
            print(manifest_path)
            return 0
        if args.command == "send":
            send_prepared_report(args.manifest, args.config)
            return 0
        if args.command == "check":
            return check_prepared_run(args.manifest)
        if args.command == "run":
            manifest_path = prepare_run(args.config, args.output_dir)
            manifest = _read_json(manifest_path)
            if manifest.get("status") != "skipped_non_trading_day":
                send_prepared_report(manifest_path, args.config)
            return check_prepared_run(manifest_path)
    except (ConfigError, DataSourceError, NotificationError, OSError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
