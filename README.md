# BNB / 黄金(XAU) 实时监控

监控 BNB 与黄金的实时价格、资金费率、持仓量(OI)，并在突破/跌破关键位、资金费率过热时预警，可选推送到手机。

## 数据源

| 数据 | 接口 |
|------|------|
| 实时价格 / 24h 涨跌 / 高低 | Binance 现货 `/api/v3/ticker/24hr` |
| 资金费率 | Binance 合约 `/fapi/v1/premiumIndex` |
| 持仓量 OI | Binance 合约 `/fapi/v1/openInterest` |

> 说明：Binance 现货没有 `XAUUSDT`，黄金价格用 **PAXG**（PAXGUSDT，1 PAXG ≈ 1 盎司实物黄金）作代理，足够用于监控关键位。需要更准的现货金价可换 MetalPriceAPI。

## 安装

**零依赖**，只用 Python 标准库，无需 `pip install`。

## 运行

```bash
python monitor.py          # 持续循环(默认每 60 秒)
python monitor.py --once   # 只跑一次(适合 cron / 调试)
```

## 📱 在 iPhone 上直接跑

任选一个 App：

**Pythonista（推荐，能弹系统通知）**
1. 把 `monitor.py` 传进 Pythonista（用「文件」App 拷进去，或 iCloud 同步）。
2. 打开 `monitor.py` 顶部 `SECRETS`，把 key 填进去（见下）。
3. 点右上角 ▶️ 运行。命中预警时会弹 **iOS 横幅通知**（无需任何额外 key）。

**a-Shell（纯终端，带颜色）**
1. 把 `monitor.py` 拷进 a-Shell。
2. 运行 `python monitor.py`。终端显示彩色表格，Ctrl+C 退出。

> iPhone 上 App 切到后台后会被系统挂起、停止刷新。要 7×24 不间断，建议 Mac mini 后台跑 + 推送到手机（Server酱/Telegram）。

## 配置

打开 `monitor.py` 顶部的 `CONFIG`，按需修改：

```python
"bnb_resistance": 750.0,   # BNB 阻力位
"bnb_support":    660.0,   # BNB 支撑位
"xau_resistance": 4600.0,  # 黄金 阻力位
"xau_support":    4400.0,  # 黄金 支撑位
"funding_hot":    0.05,    # 资金费率 > 0.05% 警惕回调
"funding_healthy":0.01,    # 资金费率 < 0.01% 健康
"refresh_seconds":60,      # 刷新间隔
```

## 预警规则

- 资金费率 `> 0.05%` → ⚠️ 杠杆过热，警惕回调
- 资金费率 `< 0.01%` → ✅ 杠杆健康
- 价格 ≥ 阻力位 → 🚀 突破信号
- 价格 ≤ 支撑位 → 📉 跌破支撑
- 24h 涨跌幅绝对值 ≥ 2% → 终端高亮

## 密钥配置（两种方式，任选其一）

**方式 A — 直接填文件（iPhone 最方便）**：打开 `monitor.py` 顶部 `SECRETS`：

```python
SECRETS = {
    "coinglass_api_key": "你的Coinglass key",  # 没有就留空, 自动跳过只用 Binance
    "serverchan_sendkey": "你的SendKey",        # 微信推送, 不用留空
    "telegram_bot_token": "你的BotToken",       # 不用留空
    "telegram_chat_id":   "你的ChatID",         # 不用留空
}
```

**方式 B — 环境变量**（Mac/Linux 更安全，不写进文件）：

```bash
export COINGLASS_API_KEY="..."
export SERVERCHAN_SENDKEY="..."
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
```

环境变量优先于文件。命中 `alert` 级信号时：Pythonista 弹本地通知 + Server酱 + Telegram 一起推。

## Coinglass 接入（可选，更专业的杠杆数据）

注册 [coinglass.com/api](https://www.coinglass.com/CryptoApi) 拿到 key，填进 `SECRETS["coinglass_api_key"]` 即生效，会额外显示并参与预警：

- **多交易所资金费率均值**（比单看币安更全面）
- **持仓量 OI（USD）**
- **多空账户比**：> 3.0 多头拥挤 / < 0.5 空头拥挤
- **24h 爆仓额**：> 500 万美元提醒踩踏风险

> 接的是 Coinglass **v4** API（`https://open-api-v4.coinglass.com`，header `CG-API-KEY`）。
> 解析做了字段容错，万一某项字段名和你的套餐对不上，程序会打印一行黄色提示并跳过该项、不影响其它数据；端点路径可在 `CONFIG` 里的 `cg_*_path` 调整。
> 没填 key 时这部分整体跳过，只用 Binance（免费、无需 key）。

## 数据记录

默认开启，每次抓取追加到 `monitor_log.csv`（`CONFIG["csv_enabled"]` 可关闭），方便回看历史。

## 后续可扩展

- 接 Coinglass API：多交易所资金费率、多空比、爆仓数据
- 接 DXY / 美债 10Y 作为黄金反向指标
- 集成财经日历，非农/CPI/议息前提醒
