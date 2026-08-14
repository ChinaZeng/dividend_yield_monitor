from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_FONT_PATH = PROJECT_ROOT / "assets" / "fonts" / "NotoSansCJKsc-Regular.otf"
DISCLAIMER = (
    "仅按近12个月已实施税前现金分红与当日收盘价机械计算；"
    "分档仓位是模型上限而非真实持仓，不考虑基本面、未来分红、税费和价格波动，"
    "不构成完整投资建议。"
)

TABLE_COLUMNS = (
    ("股票", 250),
    ("收盘价", 120),
    ("TTM每股分红", 165),
    ("TTM股息率", 145),
    ("买入阶梯｜股息率 / 价格 → 仓位", 385),
    ("卖出阶梯｜股息率 / 价格 → 上限", 385),
    ("当前档位 / 模型仓位", 275),
)


def build_report_title(snapshot: dict[str, Any]) -> str:
    display_date = snapshot.get("trade_date") or snapshot.get("report_date") or "未知日期"
    buy_signals = sum(
        1 for row in snapshot.get("stocks", []) if row.get("recommendation") == "buy_candidate"
    )
    sell_signals = sum(
        1 for row in snapshot.get("stocks", []) if row.get("recommendation") == "sell_candidate"
    )
    changes = sum(1 for row in snapshot.get("stocks", []) if _is_new_signal(row))
    errors = sum(
        1 for row in snapshot.get("stocks", []) if row.get("recommendation") == "data_error"
    )
    if snapshot.get("status") == "failed" and not snapshot.get("trade_date"):
        return f"A股股息率日报｜{display_date}｜运行失败"
    return (
        f"A股分档股息率日报｜{display_date}｜买 {buy_signals}｜"
        f"卖 {sell_signals}｜变动 {changes}｜异常 {errors}"
    )


def sorted_stock_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    def key(row: dict[str, Any]) -> tuple:
        status = row.get("recommendation")
        if status == "buy_candidate":
            return (0, -int(row.get("signal_level") or 0), row.get("code", ""))
        if status == "sell_candidate":
            return (1, -int(row.get("signal_level") or 0), row.get("code", ""))
        if status == "watch":
            distances = [
                abs(float(value))
                for value in (
                    row.get("buy_yield_gap_pp"),
                    row.get("sell_yield_gap_pp"),
                )
                if value is not None
            ]
            return (2, min(distances) if distances else 0, row.get("code", ""))
        return (3, 0, row.get("code", ""))

    return sorted(snapshot.get("stocks", []), key=key)


def build_markdown(snapshot: dict[str, Any]) -> str:
    title = build_report_title(snapshot)
    rows = sorted_stock_rows(snapshot)
    buy_signals = sum(1 for row in rows if row.get("recommendation") == "buy_candidate")
    sell_signals = sum(
        1 for row in rows if row.get("recommendation") == "sell_candidate"
    )
    changes = sum(1 for row in rows if _is_new_signal(row))
    errors = sum(1 for row in rows if row.get("recommendation") == "data_error")
    display_date = snapshot.get("trade_date") or snapshot.get("report_date") or "--"

    lines = [
        f"# {title}",
        "",
        f"- 数据日期：{display_date}",
        f"- 生成时间：{snapshot.get('generated_at', '--')}",
        f"- 监控数量：{len(rows)}",
        f"- 买入档股票：{buy_signals}",
        f"- 卖出档股票：{sell_signals}",
        f"- 新识别/跨档：{changes}",
        f"- 数据异常：{errors}",
        "",
        "| 股票 | 收盘价 | TTM每股分红(税前) | TTM股息率 | 买入阶梯（率/价→仓位） | 卖出阶梯（率/价→上限） | 当前档位 / 模型仓位 |",
        "|---|---:|---:|---:|---|---|---|",
    ]

    for row in rows:
        lines.append(
            "| {name} ({code}) | {close} | {dividend} | {yield_pct} | "
            "{buy_ladder} | {sell_ladder} | {status} |".format(
                name=_markdown_cell(row.get("name", "")),
                code=_markdown_cell(row.get("code", "")),
                close=_format_money(row.get("close")),
                dividend=_format_dividend(row.get("ttm_dividend_per_share_pre_tax")),
                yield_pct=_format_pct(row.get("dividend_yield_pct")),
                buy_ladder=_format_ladder(row.get("buy_levels", []), html_break=True),
                sell_ladder=_format_ladder(row.get("sell_levels", []), html_break=True),
                status=_markdown_cell(_status_text(row)),
            )
        )

    if snapshot.get("error"):
        lines.extend(["", f"> 运行错误：{_markdown_cell(snapshot['error'])}"])

    row_errors = [row for row in rows if row.get("error")]
    if row_errors:
        lines.extend(["", "## 数据异常详情", ""])
        for row in row_errors:
            lines.append(
                f"- `{row.get('code', '--')}` {row.get('name', '')}："
                f"{_markdown_cell(row.get('error', '未知错误'))}"
            )

    sources = snapshot.get("sources", {})
    lines.extend(
        [
            "",
            "## 计算口径",
            "",
            "TTM 窗口为 `(交易日减一个日历年, 交易日]`；仅纳入已实施且已除息的税前现金分红，"
            "并按同日及后续送股、转股比例折算到当前股份口径。",
            "模型仓位以单只股票的计划满仓为 100%；买入档只提高仓位，卖出档只降低仓位，"
            "观察区间沿用上一份快照的模型仓位。模型状态不等于券商账户真实持仓。",
            "",
            f"数据源：行情 `{sources.get('quotes', '腾讯行情')}`；"
            f"分红 `{sources.get('dividends', '东方财富/AKShare')}`。",
            "",
            f"> {DISCLAIMER}",
            "",
        ]
    )
    return "\n".join(lines)


def render_report_image(
    snapshot: dict[str, Any],
    output_path: str | Path,
    font_path: str | Path = DEFAULT_FONT_PATH,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    font_file = Path(font_path)
    if not font_file.exists():
        raise FileNotFoundError(f"报告字体不存在: {font_file}")

    fonts = {
        "title": ImageFont.truetype(str(font_file), 35),
        "summary": ImageFont.truetype(str(font_file), 22),
        "header": ImageFont.truetype(str(font_file), 17),
        "cell": ImageFont.truetype(str(font_file), 19),
        "small": ImageFont.truetype(str(font_file), 16),
    }
    rows = sorted_stock_rows(snapshot)
    row_height = 108
    width = sum(column_width for _, column_width in TABLE_COLUMNS) + 96
    table_height = 62 + max(1, len(rows)) * row_height
    height = 245 + table_height + 145

    image = Image.new("RGB", (width, height), "#f3f6fb")
    draw = ImageDraw.Draw(image)
    margin = 48

    draw.rounded_rectangle(
        (margin - 18, 24, width - margin + 18, height - 28),
        radius=24,
        fill="#ffffff",
        outline="#dce3ee",
        width=2,
    )
    draw.text(
        (margin, 52),
        build_report_title(snapshot),
        font=fonts["title"],
        fill="#172033",
    )

    buy_signals = sum(1 for row in rows if row.get("recommendation") == "buy_candidate")
    sell_signals = sum(
        1 for row in rows if row.get("recommendation") == "sell_candidate"
    )
    changes = sum(1 for row in rows if _is_new_signal(row))
    errors = sum(1 for row in rows if row.get("recommendation") == "data_error")
    summary = (
        f"数据日期  {snapshot.get('trade_date') or snapshot.get('report_date') or '--'}"
        f"    监控  {len(rows)}"
        f"    买入档  {buy_signals}"
        f"    卖出档  {sell_signals}"
        f"    新识别/跨档  {changes}"
        f"    数据异常  {errors}"
    )
    draw.text((margin, 118), summary, font=fonts["summary"], fill="#526176")
    draw.text(
        (margin, 158),
        f"生成时间  {snapshot.get('generated_at', '--')}",
        font=fonts["small"],
        fill="#7b8798",
    )

    table_x = margin
    table_y = 205
    table_width = sum(column_width for _, column_width in TABLE_COLUMNS)
    draw.rounded_rectangle(
        (table_x, table_y, table_x + table_width, table_y + table_height),
        radius=12,
        fill="#ffffff",
        outline="#dce3ee",
        width=2,
    )
    draw.rounded_rectangle(
        (table_x, table_y, table_x + table_width, table_y + 62),
        radius=12,
        fill="#263a5a",
    )
    draw.rectangle(
        (table_x, table_y + 46, table_x + table_width, table_y + 62),
        fill="#263a5a",
    )

    x = table_x
    for label, column_width in TABLE_COLUMNS:
        _draw_centered_text(
            draw,
            label,
            (x, table_y, x + column_width, table_y + 62),
            fonts["header"],
            "#ffffff",
        )
        x += column_width

    if not rows:
        _draw_centered_text(
            draw,
            "没有可展示的股票数据",
            (table_x, table_y + 62, table_x + table_width, table_y + 62 + row_height),
            fonts["cell"],
            "#8a94a6",
        )
    else:
        for index, row in enumerate(rows):
            y = table_y + 62 + index * row_height
            status = row.get("recommendation")
            if status == "buy_candidate":
                background = "#e9f8ef"
            elif status == "sell_candidate":
                background = "#fff5e8"
            elif status == "data_error":
                background = "#fff0f0"
            else:
                background = "#ffffff" if index % 2 == 0 else "#f8fafc"
            draw.rectangle((table_x, y, table_x + table_width, y + row_height), fill=background)
            draw.line((table_x, y, table_x + table_width, y), fill="#e6ebf2", width=1)

            values = (
                f"{row.get('name', '')}\n{row.get('code', '')}",
                _format_money(row.get("close")),
                _format_dividend(row.get("ttm_dividend_per_share_pre_tax")),
                _format_pct(row.get("dividend_yield_pct")),
                _format_ladder(row.get("buy_levels", [])),
                _format_ladder(row.get("sell_levels", [])),
                _status_text(row, compact=True),
            )
            x = table_x
            for column_index, ((_, column_width), value) in enumerate(zip(TABLE_COLUMNS, values)):
                color = "#172033"
                if column_index == 6:
                    color = (
                        "#137a42"
                        if status == "buy_candidate"
                        else "#b54708"
                        if status == "sell_candidate"
                        else "#b42318"
                        if status == "data_error"
                        else "#526176"
                    )
                _draw_centered_multiline_text(
                    draw,
                    value,
                    (x + 8, y + 5, x + column_width - 8, y + row_height - 5),
                    fonts["small"] if column_index in {4, 5, 6} else fonts["cell"],
                    color,
                )
                x += column_width

    footer_y = table_y + table_height + 30
    formula = "分档规则：股息率越高逐档提高模型仓位；股息率越低逐档降低持仓上限；观察区间维持上一模型仓位"
    draw.text((margin, footer_y), formula, font=fonts["small"], fill="#526176")
    disclaimer_lines = textwrap.wrap(DISCLAIMER, width=90)
    for offset, line in enumerate(disclaimer_lines):
        draw.text(
            (margin, footer_y + 34 + offset * 25),
            line,
            font=fonts["small"],
            fill="#8a5b14",
        )

    image.save(output, format="PNG", optimize=True)
    return output


def _status_text(row: dict[str, Any], compact: bool = False) -> str:
    status = row.get("recommendation")
    separator = "\n" if compact else " · "
    if status == "buy_candidate":
        level = row.get("signal_level") or "?"
        target = _format_position(row.get("model_position_pct"))
        event = _event_text(row.get("signal_event"))
        return f"买入 {level} 档{separator}模型仓位 {target}{separator}{event}"
    if status == "sell_candidate":
        level = row.get("signal_level") or "?"
        ceiling = _format_position(row.get("signal_target_position_pct"))
        model = _format_position(row.get("model_position_pct"))
        event = _event_text(row.get("signal_event"))
        return f"卖出 {level} 档{separator}上限 {ceiling} / 模型 {model}{separator}{event}"
    if status == "watch":
        model = _format_position(row.get("model_position_pct"))
        return f"观察{separator}维持模型仓位 {model}"
    error = str(row.get("error") or "未知错误")
    if compact:
        shortened = error if len(error) <= 22 else error[:21] + "…"
        return f"数据失败\n{shortened}"
    return f"数据失败：{error}"


def _format_money(value: Any) -> str:
    return "--" if value is None else f"{float(value):.2f}"


def _format_dividend(value: Any) -> str:
    return "--" if value is None else f"{float(value):.4f}"


def _format_pct(value: Any) -> str:
    return "--" if value is None else f"{float(value):.2f}%"


def _format_gap(value: Any) -> str:
    return "--" if value is None else f"{float(value):+.2f}pp"


def _format_ladder(levels: list[dict[str, Any]], html_break: bool = False) -> str:
    separator = "<br>" if html_break else "\n"
    lines = []
    for level in levels:
        lines.append(
            f"{level.get('level', '?')}档  {_format_pct(level.get('yield_threshold_pct'))} / "
            f"¥{_format_money(level.get('target_price'))} → "
            f"{_format_position(level.get('target_position_pct'))}"
        )
    return separator.join(lines) if lines else "--"


def _format_position(value: Any) -> str:
    if value is None:
        return "--"
    number = float(value)
    return f"{number:.0f}%" if number.is_integer() else f"{number:.1f}%"


def _event_text(event: Any) -> str:
    return {
        "initial": "首次识别",
        "crossed": "新跨档",
        "maintain": "维持档位",
        "none": "无新动作",
    }.get(str(event), "--")


def _is_new_signal(row: dict[str, Any]) -> bool:
    return row.get("signal_event") in {"initial", "crossed"} and row.get(
        "recommendation"
    ) in {"buy_candidate", "sell_candidate"}


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[float, float, float, float],
    font: ImageFont.FreeTypeFont,
    fill: str,
) -> None:
    left, top, right, bottom = box
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text(
        (left + (right - left - width) / 2, top + (bottom - top - height) / 2 - bounds[1]),
        text,
        font=font,
        fill=fill,
    )


def _draw_centered_multiline_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[float, float, float, float],
    font: ImageFont.FreeTypeFont,
    fill: str,
) -> None:
    left, top, right, bottom = box
    spacing = 4
    bounds = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align="center")
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.multiline_text(
        (left + (right - left - width) / 2, top + (bottom - top - height) / 2 - bounds[1]),
        text,
        font=font,
        fill=fill,
        spacing=spacing,
        align="center",
    )
