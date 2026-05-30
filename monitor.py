#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BNB / 黄金(XAU) 实时监控与预警

数据源:
  - Binance 现货 24hr ticker  : https://api.binance.com/api/v3/ticker/24hr
  - Binance 合约 资金费率/标记价: https://fapi.binance.com/fapi/v1/premiumIndex
  - Binance 合约 持仓量(OI)   : https://fapi.binance.com/fapi/v1/openInterest

用法:
  python monitor.py            # 按 CONFIG["refresh_seconds"] 循环
  python monitor.py --once     # 只抓取并打印一次(适合 cron / 调试)

手机推送(可选, 通过环境变量开启):
  Server酱(微信): 设置 SERVERCHAN_SENDKEY
  Telegram      : 设置 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone

import requests

# ========================= 配置区 =========================
CONFIG = {
    # ---- 关键价位 (按需修改) ----
    "bnb_resistance": 750.0,   # BNB 阻力位
    "bnb_support": 660.0,      # BNB 支撑位
    "xau_resistance": 4600.0,  # 黄金 阻力位
    "xau_support": 4400.0,     # 黄金 支撑位

    # ---- 资金费率阈值 (单位: %) ----
    "funding_hot": 0.05,       # > 该值 => 杠杆过热, 警惕回调
    "funding_healthy": 0.01,   # < 该值 => 杠杆健康

    # ---- 运行参数 ----
    "refresh_seconds": 60,     # 刷新间隔(秒)
    "highlight_change": 2.0,   # 24h 涨跌幅绝对值超过该值则高亮
    "request_timeout": 10,     # 单次请求超时(秒)

    # ---- 数据记录 ----
    "csv_enabled": True,
    "csv_path": "monitor_log.csv",
}

# 现货标的: 黄金在 Binance 现货并没有 XAUUSDT, 仅币安合约/部分平台有.
# 这里 BNB 走现货+合约; XAU 默认尝试 Binance, 抓不到会自动跳过并提示.
SPOT_SYMBOLS = {
    "BNB": "BNBUSDT",
    "XAU": "PAXGUSDT",  # PAXG≈1盎司黄金, 作为 Binance 上可得的黄金价格代理
}
# 是否对该标的获取合约资金费率/持仓量
HAS_FUTURES = {"BNB": True, "XAU": False}

# 终端颜色
COLOR = {
    "reset": "\033[0m", "bold": "\033[1m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "cyan": "\033[36m", "magenta": "\033[35m",
}


def c(text, *styles):
    """给文本套终端颜色; 非 TTY 时返回纯文本."""
    if not sys.stdout.isatty():
        return text
    return "".join(COLOR[s] for s in styles) + text + COLOR["reset"]


# ========================= 数据获取 =========================
SPOT_BASE = "https://api.binance.com"
FUT_BASE = "https://fapi.binance.com"


def _get_json(url, params=None):
    resp = requests.get(url, params=params, timeout=CONFIG["request_timeout"])
    resp.raise_for_status()
    return resp.json()


def get_price(symbol):
    """24hr ticker -> 最新价/涨跌幅/高/低/成交额."""
    d = _get_json(f"{SPOT_BASE}/api/v3/ticker/24hr", {"symbol": symbol})
    return {
        "price": float(d["lastPrice"]),
        "change": float(d["priceChangePercent"]),
        "high": float(d["highPrice"]),
        "low": float(d["lowPrice"]),
        "quote_volume": float(d["quoteVolume"]),
    }


def get_funding_rate(symbol):
    """合约资金费率, 转成百分比(%)."""
    d = _get_json(f"{FUT_BASE}/fapi/v1/premiumIndex", {"symbol": symbol})
    return float(d["lastFundingRate"]) * 100.0


def get_open_interest(symbol):
    """合约持仓量(以合约标的数量计)."""
    d = _get_json(f"{FUT_BASE}/fapi/v1/openInterest", {"symbol": symbol})
    return float(d["openInterest"])


# ========================= 信号判断 =========================
def check_signals(name, price_data, funding=None):
    """返回 [(级别, 文本)] 列表, 级别用于决定是否推送."""
    signals = []
    price = price_data["price"]
    resistance = CONFIG[f"{name.lower()}_resistance"]
    support = CONFIG[f"{name.lower()}_support"]

    # 价格突破/跌破
    if price >= resistance:
        signals.append(("alert", f"🚀 {name} 突破阻力位 {resistance} (现价 {price:g})"))
    elif price <= support:
        signals.append(("alert", f"📉 {name} 跌破支撑位 {support} (现价 {price:g})"))

    # 资金费率(仅合约标的)
    if funding is not None:
        if funding > CONFIG["funding_hot"]:
            signals.append(("alert", f"⚠️ {name} 资金费率 {funding:.4f}% 过热, 警惕回调"))
        elif funding < CONFIG["funding_healthy"]:
            signals.append(("info", f"✅ {name} 资金费率 {funding:.4f}% 健康"))

    return signals


# ========================= 推送通知 =========================
def push_serverchan(title, body):
    key = os.getenv("SERVERCHAN_SENDKEY")
    if not key:
        return
    try:
        requests.post(
            f"https://sctapi.ftqq.com/{key}.send",
            data={"title": title, "desp": body},
            timeout=CONFIG["request_timeout"],
        )
    except requests.RequestException as e:
        print(c(f"[推送] Server酱失败: {e}", "red"))


def push_telegram(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=CONFIG["request_timeout"],
        )
    except requests.RequestException as e:
        print(c(f"[推送] Telegram失败: {e}", "red"))


def notify(signals):
    """只推送 alert 级别的信号."""
    alerts = [text for level, text in signals if level == "alert"]
    if not alerts:
        return
    title = "📡 行情预警"
    body = "\n".join(alerts)
    push_serverchan(title, body)
    push_telegram(f"{title}\n{body}")


# ========================= 数据记录 =========================
def log_csv(ts, name, pd, funding, oi):
    if not CONFIG["csv_enabled"]:
        return
    path = CONFIG["csv_path"]
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time", "name", "price", "change_24h_pct",
                        "high", "low", "funding_pct", "open_interest"])
        w.writerow([ts, name, pd["price"], pd["change"], pd["high"],
                    pd["low"], "" if funding is None else funding,
                    "" if oi is None else oi])


# ========================= 抓取一个标的 =========================
def fetch_one(name):
    symbol = SPOT_SYMBOLS[name]
    pd = get_price(symbol)
    funding = oi = None
    if HAS_FUTURES.get(name):
        try:
            funding = get_funding_rate(symbol)
            oi = get_open_interest(symbol)
        except requests.RequestException as e:
            print(c(f"[{name}] 合约数据获取失败: {e}", "yellow"))
    return pd, funding, oi


def render_row(name, pd, funding, oi):
    change = pd["change"]
    arrow = "▲" if change >= 0 else "▼"
    change_str = f"{arrow}{abs(change):.2f}%"
    if abs(change) >= CONFIG["highlight_change"]:
        change_str = c(change_str, "bold", "green" if change >= 0 else "red")
    else:
        change_str = c(change_str, "green" if change >= 0 else "red")

    fund_str = "-" if funding is None else f"{funding:.4f}%"
    oi_str = "-" if oi is None else f"{oi:,.0f}"
    print(f"  {c(name.ljust(4), 'cyan', 'bold')} "
          f"价 {pd['price']:>12,.4g}  涨跌 {change_str:>14}  "
          f"高 {pd['high']:>10,.4g}  低 {pd['low']:>10,.4g}  "
          f"费率 {fund_str:>10}  OI {oi_str:>14}")


# ========================= 主流程 =========================
def run_once():
    now = datetime.now()
    ts_iso = datetime.now(timezone.utc).isoformat()
    print(f"\n{'=' * 78}")
    print(f"⏰ {now.strftime('%Y-%m-%d %H:%M:%S')}")

    all_signals = []
    for name in SPOT_SYMBOLS:
        try:
            pd, funding, oi = fetch_one(name)
        except requests.RequestException as e:
            print(c(f"  {name} 获取失败: {e}", "red"))
            continue
        render_row(name, pd, funding, oi)
        log_csv(ts_iso, name, pd, funding, oi)
        all_signals.extend(check_signals(name, pd, funding))

    if all_signals:
        print("  " + "-" * 40)
        for level, text in all_signals:
            print("  " + (c(text, "yellow", "bold") if level == "alert" else text))
        notify(all_signals)


def main():
    parser = argparse.ArgumentParser(description="BNB/黄金 实时监控")
    parser.add_argument("--once", action="store_true", help="只运行一次后退出")
    args = parser.parse_args()

    if args.once:
        run_once()
        return

    print(c("🚀 监控启动, Ctrl+C 退出", "bold", "magenta"))
    while True:
        try:
            run_once()
        except Exception as e:  # 单轮异常不应终止整个循环
            print(c(f"[循环异常] {e}", "red"))
        try:
            time.sleep(CONFIG["refresh_seconds"])
        except KeyboardInterrupt:
            print("\n👋 已退出")
            break


if __name__ == "__main__":
    main()
