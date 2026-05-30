#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BNB / 黄金(XAU) 实时监控与预警 (iPhone 可直接运行版)

零依赖: 只用 Python 标准库(urllib), 在 Pythonista / a-Shell 里免安装即可跑。
也可在 Mac / Linux 直接 python monitor.py。

数据源:
  - Binance 现货 24hr ticker  : https://api.binance.com/api/v3/ticker/24hr
  - Binance 合约 资金费率      : https://fapi.binance.com/fapi/v1/premiumIndex
  - Binance 合约 持仓量(OI)   : https://fapi.binance.com/fapi/v1/openInterest
  - Coinglass v4 (可选, 需 key): 多交易所资金费率 / OI / 多空比 / 爆仓

用法:
  python monitor.py            # 循环刷新
  python monitor.py --once     # 只跑一次

密钥(二选一):
  方式A 直接填下面的 SECRETS 字典(iPhone 最方便)
  方式B 设环境变量: COINGLASS_API_KEY / SERVERCHAN_SENDKEY /
        TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# 网络异常统一捕获
NET_ERRORS = (HTTPError, URLError, TimeoutError, ValueError, OSError)

# ========================= 密钥区 (iPhone 直接填这里) =========================
SECRETS = {
    "coinglass_api_key": "",   # 注册 coinglass.com/api 拿到 key 填这里
    "serverchan_sendkey": "",  # Server酱(微信推送) SendKey, 不用留空
    "telegram_bot_token": "",  # Telegram 机器人 token, 不用留空
    "telegram_chat_id": "",    # Telegram chat id, 不用留空
}


def secret(name):
    """环境变量优先, 其次 SECRETS 字典。"""
    return os.getenv(name.upper(), "").strip() or SECRETS.get(name, "").strip()


# ========================= 配置区 =========================
CONFIG = {
    # ---- 关键价位 (按需修改) ----
    "bnb_resistance": 750.0,
    "bnb_support": 660.0,
    "xau_resistance": 4600.0,
    "xau_support": 4400.0,

    # ---- 资金费率阈值 (单位: %) ----
    "funding_hot": 0.05,       # > 该值 => 杠杆过热, 警惕回调
    "funding_healthy": 0.01,   # < 该值 => 杠杆健康

    # ---- Coinglass 阈值 ----
    "ls_ratio_high": 3.0,      # 多空比 > 该值 => 多头过度拥挤
    "ls_ratio_low": 0.5,       # 多空比 < 该值 => 空头过度拥挤
    "liq_alert_usd": 5_000_000,  # 24h 爆仓额 > 该值 => 踩踏风险提醒
    "coinglass_symbol": "BNB",

    # ---- 运行参数 ----
    "refresh_seconds": 60,
    "highlight_change": 2.0,
    "request_timeout": 12,

    # ---- 数据记录 ----
    "csv_enabled": True,
    "csv_path": "monitor_log.csv",

    # ---- Coinglass v4 端点 (字段对不上时可在此调整) ----
    "cg_base": "https://open-api-v4.coinglass.com",
    "cg_funding_path": "/api/futures/funding-rate/exchange-list",
    "cg_oi_path": "/api/futures/open-interest/exchange-list",
    "cg_ls_path": "/api/futures/global-long-short-account-ratio/history",
    "cg_liq_path": "/api/futures/liquidation/coin-list",
}

SPOT_SYMBOLS = {
    "BNB": "BNBUSDT",
    "XAU": "PAXGUSDT",  # PAXG≈1盎司黄金, Binance 上可得的黄金价格代理
}
HAS_FUTURES = {"BNB": True, "XAU": False}

# ========================= iPhone 本地通知 (Pythonista) =========================
try:
    import notification as _ios_notification  # Pythonista 自带
    _HAS_IOS_NOTIFY = True
except ImportError:
    _ios_notification = None
    _HAS_IOS_NOTIFY = False

# ========================= 终端颜色 =========================
COLOR = {
    "reset": "\033[0m", "bold": "\033[1m",
    "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
    "cyan": "\033[36m", "magenta": "\033[35m",
}


def c(text, *styles):
    """套终端颜色; 非 TTY(如 Pythonista 控制台)时返回纯文本。"""
    if not sys.stdout.isatty():
        return text
    return "".join(COLOR[s] for s in styles) + text + COLOR["reset"]


# ========================= HTTP (标准库, 零依赖) =========================
def _get_json(url, params=None, headers=None):
    if params:
        url = f"{url}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "bnb-gold-monitor/1.0",
                                "Accept": "application/json",
                                **(headers or {})})
    with urlopen(req, timeout=CONFIG["request_timeout"]) as r:
        return json.loads(r.read().decode("utf-8"))


# ========================= Binance 数据 =========================
SPOT_BASE = "https://api.binance.com"
FUT_BASE = "https://fapi.binance.com"


def get_price(symbol):
    d = _get_json(f"{SPOT_BASE}/api/v3/ticker/24hr", {"symbol": symbol})
    return {
        "price": float(d["lastPrice"]),
        "change": float(d["priceChangePercent"]),
        "high": float(d["highPrice"]),
        "low": float(d["lowPrice"]),
        "quote_volume": float(d["quoteVolume"]),
    }


def get_funding_rate(symbol):
    d = _get_json(f"{FUT_BASE}/fapi/v1/premiumIndex", {"symbol": symbol})
    return float(d["lastFundingRate"]) * 100.0


def get_open_interest(symbol):
    d = _get_json(f"{FUT_BASE}/fapi/v1/openInterest", {"symbol": symbol})
    return float(d["openInterest"])


# ========================= Coinglass v4 (可选) =========================
def _cg_get(path, params=None):
    """带 key 调 Coinglass, 返回 data 字段; 失败/无 key 抛异常或返回 None。"""
    key = secret("coinglass_api_key")
    if not key:
        return None
    payload = _get_json(CONFIG["cg_base"] + path, params,
                        headers={"CG-API-KEY": key})
    # 标准信封: {"code":"0","msg":"success","data":[...]}
    if isinstance(payload, dict):
        code = str(payload.get("code", "0"))
        if code not in ("0", "200", "success"):
            raise ValueError(f"Coinglass 返回 code={code} msg={payload.get('msg')}")
        return payload.get("data", payload)
    return payload


def _collect_numbers(obj, keys):
    """递归收集 obj 中命中 keys 的所有数值, 用于跨结构容错解析。"""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, (int, float)):
                out.append(float(v))
            elif k in keys and isinstance(v, str):
                try:
                    out.append(float(v))
                except ValueError:
                    pass
            else:
                out.extend(_collect_numbers(v, keys))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(_collect_numbers(item, keys))
    return out


def _find_entry(data, symbol):
    """在列表里找 symbol 对应的条目(币种维度的接口)。"""
    if not isinstance(data, list):
        return data
    sym = symbol.upper()
    for item in data:
        if not isinstance(item, dict):
            continue
        s = str(item.get("symbol") or item.get("coin") or "").upper()
        if s == sym:
            return item
    return None


def get_coinglass(symbol):
    """返回 {funding_avg, oi_usd, ls_ratio, liq_long, liq_short, liq_total},
    缺失项为 None。任何子项失败都不影响其它项。"""
    out = {"funding_avg": None, "oi_usd": None, "ls_ratio": None,
           "liq_long": None, "liq_short": None, "liq_total": None}
    if not secret("coinglass_api_key"):
        return out

    # 1) 多交易所资金费率 -> 取均值(%)
    try:
        data = _cg_get(CONFIG["cg_funding_path"], {"symbol": symbol})
        rates = _collect_numbers(data, {"funding_rate", "fundingRate"})
        if rates:
            out["funding_avg"] = sum(rates) / len(rates)
    except NET_ERRORS as e:
        _cg_warn("funding", e)

    # 2) 持仓量 OI(USD) -> 取最大(通常是聚合/All)
    try:
        data = _cg_get(CONFIG["cg_oi_path"], {"symbol": symbol})
        ois = _collect_numbers(data, {"open_interest_usd", "openInterestUsd",
                                    "open_interest", "openInterest"})
        if ois:
            out["oi_usd"] = max(ois)
    except NET_ERRORS as e:
        _cg_warn("open_interest", e)

    # 3) 多空账户比 -> 取最新一个点
    try:
        data = _cg_get(CONFIG["cg_ls_path"],
                    {"symbol": symbol, "interval": "h1", "limit": 1})
        ratios = _collect_numbers(
            data, {"global_account_long_short_ratio", "long_short_ratio",
                    "longShortRatio"})
        if ratios:
            out["ls_ratio"] = ratios[-1]
    except NET_ERRORS as e:
        _cg_warn("long_short", e)

    # 4) 24h 爆仓
    try:
        data = _cg_get(CONFIG["cg_liq_path"])
        entry = _find_entry(data, symbol) or data
        longs = _collect_numbers(entry, {"long_liquidation_usd_24h",
                                        "longLiquidationUsd24h",
                                        "long_liquidation_usd"})
        shorts = _collect_numbers(entry, {"short_liquidation_usd_24h",
                                        "shortLiquidationUsd24h",
                                        "short_liquidation_usd"})
        total = _collect_numbers(entry, {"liquidation_usd_24h",
                                        "liquidationUsd24h"})
        out["liq_long"] = longs[0] if longs else None
        out["liq_short"] = shorts[0] if shorts else None
        if total:
            out["liq_total"] = total[0]
        elif longs or shorts:
            out["liq_total"] = (longs[0] if longs else 0) + (shorts[0] if shorts else 0)
    except NET_ERRORS as e:
        _cg_warn("liquidation", e)

    return out


_CG_WARNED = set()


def _cg_warn(metric, err):
    """同一类错误只提示一次, 避免刷屏。"""
    if metric not in _CG_WARNED:
        print(c(f"[Coinglass] {metric} 暂不可用({err}); 字段可在 CONFIG 里调整", "yellow"))
        _CG_WARNED.add(metric)


# ========================= 信号判断 =========================
def check_signals(name, price_data, funding=None, cg=None):
    signals = []
    price = price_data["price"]
    resistance = CONFIG[f"{name.lower()}_resistance"]
    support = CONFIG[f"{name.lower()}_support"]

    if price >= resistance:
        signals.append(("alert", f"🚀 {name} 突破阻力位 {resistance} (现价 {price:g})"))
    elif price <= support:
        signals.append(("alert", f"📉 {name} 跌破支撑位 {support} (现价 {price:g})"))

    # 资金费率: Coinglass 多所均值优先, 否则 Binance
    fr = cg["funding_avg"] if (cg and cg.get("funding_avg") is not None) else funding
    if fr is not None:
        if fr > CONFIG["funding_hot"]:
            signals.append(("alert", f"⚠️ {name} 资金费率 {fr:.4f}% 过热, 警惕回调"))
        elif fr < CONFIG["funding_healthy"]:
            signals.append(("info", f"✅ {name} 资金费率 {fr:.4f}% 健康"))

    # Coinglass 衍生信号
    if cg:
        lsr = cg.get("ls_ratio")
        if lsr is not None:
            if lsr > CONFIG["ls_ratio_high"]:
                signals.append(("alert", f"⚠️ {name} 多空比 {lsr:.2f} 偏高, 多头拥挤"))
            elif lsr < CONFIG["ls_ratio_low"]:
                signals.append(("alert", f"⚠️ {name} 多空比 {lsr:.2f} 偏低, 空头拥挤"))
        liq = cg.get("liq_total")
        if liq is not None and liq > CONFIG["liq_alert_usd"]:
            signals.append(("alert", f"💥 {name} 24h 爆仓 ${liq:,.0f}, 踩踏风险"))

    return signals


# ========================= 推送通知 =========================
def push_serverchan(title, body):
    key = secret("serverchan_sendkey")
    if not key:
        return
    try:
        data = urlencode({"title": title, "desp": body}).encode()
        req = Request(f"https://sctapi.ftqq.com/{key}.send", data=data,
                    headers={"User-Agent": "monitor/1.0"})
        urlopen(req, timeout=CONFIG["request_timeout"]).read()
    except NET_ERRORS as e:
        print(c(f"[推送] Server酱失败: {e}", "red"))


def push_telegram(text):
    token = secret("telegram_bot_token")
    chat_id = secret("telegram_chat_id")
    if not (token and chat_id):
        return
    try:
        data = urlencode({"chat_id": chat_id, "text": text}).encode()
        req = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        urlopen(req, timeout=CONFIG["request_timeout"]).read()
    except NET_ERRORS as e:
        print(c(f"[推送] Telegram失败: {e}", "red"))


def push_ios(title, body):
    """Pythonista 本地横幅通知(无需任何 key, iPhone 直接弹)。"""
    if not _HAS_IOS_NOTIFY:
        return
    try:
        _ios_notification.schedule(f"{title}\n{body}", delay=1)
    except Exception:
        pass


def notify(signals):
    alerts = [text for level, text in signals if level == "alert"]
    if not alerts:
        return
    title = "📡 行情预警"
    body = "\n".join(alerts)
    push_ios(title, body)
    push_serverchan(title, body)
    push_telegram(f"{title}\n{body}")


# ========================= 数据记录 =========================
def log_csv(ts, name, pd, funding, cg):
    if not CONFIG["csv_enabled"]:
        return
    path = CONFIG["csv_path"]
    new_file = not os.path.exists(path)
    cg = cg or {}
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time", "name", "price", "change_24h_pct", "high", "low",
                        "funding_pct", "cg_funding_avg", "cg_oi_usd",
                        "cg_ls_ratio", "cg_liq_total"])
        w.writerow([ts, name, pd["price"], pd["change"], pd["high"], pd["low"],
                    "" if funding is None else funding,
                    _csv_val(cg.get("funding_avg")), _csv_val(cg.get("oi_usd")),
                    _csv_val(cg.get("ls_ratio")), _csv_val(cg.get("liq_total"))])


def _csv_val(v):
    return "" if v is None else v


# ========================= 抓取 + 渲染 =========================
def fetch_one(name):
    symbol = SPOT_SYMBOLS[name]
    pd = get_price(symbol)
    funding = None
    if HAS_FUTURES.get(name):
        try:
            funding = get_funding_rate(symbol)
        except NET_ERRORS as e:
            print(c(f"[{name}] 资金费率获取失败: {e}", "yellow"))
    cg = None
    if HAS_FUTURES.get(name) and secret("coinglass_api_key"):
        cg = get_coinglass(CONFIG["coinglass_symbol"])
    return pd, funding, cg


def render_row(name, pd, funding, cg):
    change = pd["change"]
    arrow = "▲" if change >= 0 else "▼"
    change_str = f"{arrow}{abs(change):.2f}%"
    style = ("green",) if change >= 0 else ("red",)
    if abs(change) >= CONFIG["highlight_change"]:
        style = ("bold",) + style
    change_str = c(change_str, *style)

    fr = cg["funding_avg"] if (cg and cg.get("funding_avg") is not None) else funding
    fund_str = "-" if fr is None else f"{fr:.4f}%"
    print(f"  {c(name.ljust(4), 'cyan', 'bold')} "
          f"价 {pd['price']:>12,.4g}  涨跌 {change_str:>14}  "
          f"高 {pd['high']:>10,.4g}  低 {pd['low']:>10,.4g}  "
          f"费率 {fund_str:>10}")
    if cg:
        parts = []
        if cg.get("oi_usd") is not None:
            parts.append(f"OI ${cg['oi_usd']:,.0f}")
        if cg.get("ls_ratio") is not None:
            parts.append(f"多空比 {cg['ls_ratio']:.2f}")
        if cg.get("liq_total") is not None:
            parts.append(f"24h爆仓 ${cg['liq_total']:,.0f}")
        if parts:
            print(f"       {c('└ Coinglass:', 'magenta')} " + "  ".join(parts))


# ========================= 主流程 =========================
def run_once():
    now = datetime.now()
    ts_iso = datetime.now(timezone.utc).isoformat()
    print(f"\n{'=' * 78}")
    print(f"⏰ {now.strftime('%Y-%m-%d %H:%M:%S')}")

    all_signals = []
    for name in SPOT_SYMBOLS:
        try:
            pd, funding, cg = fetch_one(name)
        except NET_ERRORS as e:
            print(c(f"  {name} 获取失败: {e}", "red"))
            continue
        render_row(name, pd, funding, cg)
        log_csv(ts_iso, name, pd, funding, cg)
        all_signals.extend(check_signals(name, pd, funding, cg))

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
    if secret("coinglass_api_key"):
        print(c("   Coinglass 已启用", "magenta"))
    while True:
        try:
            run_once()
        except Exception as e:
            print(c(f"[循环异常] {e}", "red"))
        try:
            time.sleep(CONFIG["refresh_seconds"])
        except KeyboardInterrupt:
            print("\n👋 已退出")
            break


if __name__ == "__main__":
    main()
