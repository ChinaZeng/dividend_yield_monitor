# A 股股息率监控

这是一个面向个人自选股的收盘后监控脚本。它在每个 A 股交易日生成股息率日报，按每只股票自己的三档买入、三档卖出阶梯给出模型仓位信号，并通过 QQ 邮箱发送内嵌图片和 Markdown 附件。

脚本只生成规则信号，不会连接券商或自动下单。

## 计算口径

税前 TTM 股息率按以下公式计算：

```text
近 12 个月已实施现金分红（当前股份口径） ÷ 当日收盘价 × 100%
```

- TTM 窗口为 `(交易日减一个日历年, 交易日]`。
- 只纳入方案进度为“实施分配”且已经除息的税前现金分红。
- 历史每股分红会按同日及后续送股、转股比例折算到当前股份口径。
- 行情来自腾讯公开行情接口；分红详情通过 AKShare 的东方财富接口获取。
- 免费网页数据源可能变更，数据异常时不会沿用上一日结果。

## 安装

项目目标运行环境为 Python 3.11：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

完整的 Noto Sans CJK SC 字体随项目放在 `assets/fonts/`，用于在 GitHub Actions 中稳定生成中文 PNG。字体采用 SIL Open Font License 1.1。

## 配置自选股

编辑 `config.json`：

```json
{
  "timezone": "Asia/Shanghai",
  "stocks": [
    {
      "code": "601288.SH",
      "name": "农业银行",
      "buy_levels": [
        {"yield_threshold_pct": 5.0, "target_position_pct": 25},
        {"yield_threshold_pct": 5.5, "target_position_pct": 60},
        {"yield_threshold_pct": 6.0, "target_position_pct": 100}
      ],
      "sell_levels": [
        {"yield_threshold_pct": 3.9, "target_position_pct": 70},
        {"yield_threshold_pct": 3.6, "target_position_pct": 35},
        {"yield_threshold_pct": 3.3, "target_position_pct": 0}
      ]
    }
  ],
  "notifier": {
    "type": "qqmail",
    "address_env": "QQ_EMAIL_ADDRESS",
    "auth_code_env": "QQ_EMAIL_AUTH_CODE"
  }
}
```

完整五只股票参数见仓库中的 `config.json`。股票代码必须使用 `六位代码.交易所` 格式，支持 `.SH`、`.SZ` 和 `.BJ`，代码不能重复。买卖两侧必须各有三档：买入股息率和目标仓位逐档升高，卖出股息率和持仓上限逐档降低，而且最高卖出线必须低于最低买入线。

信号规则：

```text
股息率逐档升高  → 买入 1/2/3 档，模型仓位最多提高至 25%/60%/100%
股息率逐档降低  → 卖出 1/2/3 档，持仓上限降低至 70%/35%/0%
买卖区间之间    → 观察并沿用上一份快照中的模型仓位
```

这是因为分红金额不变时，股价下跌会推高股息率，股价上涨会压低股息率。程序保存的是模型仓位，不是券商账户真实仓位：买入档只允许提高模型仓位，卖出档只允许降低模型仓位，因此较低仓位遇到“持仓上限 70%”时不会反向补仓。同一档连续出现时标记为“维持档位”，只有首次识别或跨档才算新信号。

当前五只股票的卖出三档约取近三年每日 TTM 股息率 P30/P20/P10 的低位区间并取整；买入档以历史中高分位和原买入线为中心设置。参数不会自动漂移。每档参考价按 `TTM 每股分红 ÷ 该档股息率` 每日重算；公司削减或增加分红时，参考价也会同步变化。

旧配置中的单一 `yield_threshold_pct` 或 `buy_yield_threshold_pct` / `sell_yield_threshold_pct` 仍可读取为兼容模式，但不会获得完整六档能力。

## 配置 QQ 邮箱

登录 QQ 邮箱网页版，在账号设置中开启 SMTP 服务并生成授权码。授权码不是 QQ 密码，不能写入 `config.json` 或提交到仓库。

本地运行前设置：

```bash
export QQ_EMAIL_ADDRESS="123456789@qq.com"
export QQ_EMAIL_AUTH_CODE="your-smtp-authorization-code"
```

发件人与收件人使用同一个 QQ 邮箱。程序固定连接 `smtp.qq.com:465` 并使用 SSL。

## 运行

完整运行一次，包括生成、发信和健康检查：

```bash
python monitor.py run
```

也可以按 GitHub Actions 相同的三阶段运行：

```bash
python monitor.py prepare --output-dir .report
python monitor.py send --manifest .report/manifest.json
python monitor.py check --manifest .report/manifest.json
```

- `prepare` 会生成每日 JSON、Markdown、PNG 和 manifest。
- `send` 从 manifest 读取同一批报告文件并发送邮件。
- `check` 对 `partial` 或 `failed` 返回非零退出码。
- 非交易日状态为 `skipped_non_trading_day`，不生成日报、不发送邮件。
- 当日 15:00 前手动运行不会把盘中价当作收盘价，而会生成失败报告。

每日快照保存在 `data/YYYY-MM-DD.json`。同一天重跑会原子覆盖当天文件；Markdown 和 PNG 默认保存在 `.report/`。

## GitHub Actions

工作流在周一至周五北京时间 16:30 运行，也支持手动触发。先在仓库 **Settings → Secrets and variables → Actions** 中添加：

- `QQ_EMAIL_ADDRESS`：完整 QQ 邮箱地址。
- `QQ_EMAIL_AUTH_CODE`：QQ 邮箱 SMTP 授权码。

工作流需要 `contents: write` 以提交 `data/YYYY-MM-DD.json`。Markdown、PNG 和 manifest 作为 Actions Artifact 保留 30 天，不提交到仓库。

部分股票失败时，工作流仍会提交当日 JSON、上传 Artifact 并发送带错误行的邮件，最后在健康检查步骤标红。

## 测试

```bash
python -m unittest discover -s tests -v
python -m py_compile *.py tests/*.py
```

测试使用固定响应，不会发送真实邮件，也不会依赖实时行情数值。

> 本报告仅按历史分红与收盘价机械计算，不考虑基本面、未来分红、税费和价格波动，不构成完整投资建议。
