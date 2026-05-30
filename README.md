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

```bash
pip install -r requirements.txt
```

## 运行

```bash
python monitor.py          # 持续循环(默认每 60 秒)
python monitor.py --once   # 只跑一次(适合 cron / 调试)
```

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

## 手机推送（可选）

通过环境变量开启，命中 `alert` 级信号时推送：

```bash
# Server酱(微信)
export SERVERCHAN_SENDKEY="你的SendKey"

# Telegram
export TELEGRAM_BOT_TOKEN="你的BotToken"
export TELEGRAM_CHAT_ID="你的ChatID"
```

## 数据记录

默认开启，每次抓取追加到 `monitor_log.csv`（`CONFIG["csv_enabled"]` 可关闭），方便回看历史。

## 后续可扩展

- 接 Coinglass API：多交易所资金费率、多空比、爆仓数据
- 接 DXY / 美债 10Y 作为黄金反向指标
- 集成财经日历，非农/CPI/议息前提醒
