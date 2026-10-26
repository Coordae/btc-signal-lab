import json
import math
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Polymarket BTC 5m Assistant", page_icon="₿", layout="wide")

BINANCE = "https://data-api.binance.vision"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
HEADERS = {"User-Agent": "BTC-5m-Assistant/1.0"}

st.markdown("""
<style>
.block-container {max-width: 1200px; padding-top: 1.4rem; padding-bottom: 2rem;}
div[data-testid="stMetric"] {background: rgba(128,128,128,.08); padding: 12px; border-radius: 12px;}
</style>
""", unsafe_allow_html=True)

st.title("₿ Polymarket BTC 5-Minute Assistant")
st.caption("Market-aware trading research dashboard · signals only · no orders placed")

with st.sidebar:
    st.header("Controls")
    refresh = st.selectbox("Refresh interval", [5, 10, 15, 30], index=1, format_func=lambda x: f"{x} seconds")
    threshold = st.slider("Minimum preliminary model probability", 0.52, 0.75, 0.60, 0.01)
    st.caption("This threshold is a display filter, not a guarantee of edge.")
    st.divider()
    st.markdown("**Data:** Binance public BTC/USDT + Polymarket public market data")
    st.markdown("**Trading:** disabled; no wallet or API keys required.")

st_autorefresh(interval=refresh * 1000, key="refresh")


def get_json(url, params=None):
    r = requests.get(url, params=params, headers=HEADERS, timeout=8)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=5, show_spinner=False)
def get_btc():
    price = float(get_json(f"{BINANCE}/api/v3/ticker/price", {"symbol": "BTCUSDT"})["price"])
    raw = get_json(f"{BINANCE}/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": 90})
    cols = ["open_time", "open", "high", "low", "close", "volume", "close_time",
            "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
    df = pd.DataFrame(raw, columns=cols)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return price, df


@st.cache_data(ttl=5, show_spinner=False)
def get_current_market():
    events = get_json(
        f"{GAMMA}/events",
        {"series_slug": "btc-up-or-down-5m", "closed": "false", "limit": 100,
         "order": "endDate", "ascending": "true"}
    )
    if isinstance(events, dict):
        events = [events]
    now = datetime.now(timezone.utc)
    candidates = []
    for event in events or []:
        for market in event.get("markets", []) or []:
            if market.get("closed") or market.get("active") is False:
                continue
            start = market.get("eventStartTime") or event.get("startDate") or market.get("startDate")
            end = market.get("endDate") or event.get("endDate")
            try:
                end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
            except Exception:
                continue
            if end_dt <= now or (start_dt and start_dt > now):
                continue
            tokens = market.get("clobTokenIds", [])
            outcomes = market.get("outcomes", [])
            if isinstance(tokens, str):
                try: tokens = json.loads(tokens)
                except Exception: tokens = []
            if isinstance(outcomes, str):
                try: outcomes = json.loads(outcomes)
                except Exception: outcomes = []
            if len(tokens) < 2:
                continue
            # Map tokens by outcome labels where possible; most BTC Up/Down markets use Up, Down order.
            up_idx, down_idx = 0, 1
            for i, outcome in enumerate(outcomes):
                if str(outcome).strip().lower() == "up": up_idx = i
                if str(outcome).strip().lower() == "down": down_idx = i
            if max(up_idx, down_idx) >= len(tokens):
                continue
            candidates.append({
                "event": event, "market": market, "start": start_dt, "end": end_dt,
                "up_token": str(tokens[up_idx]), "down_token": str(tokens[down_idx]),
                "question": market.get("question") or event.get("title") or "BTC Up or Down?",
                "slug": market.get("slug") or event.get("slug"),
                "url": ("https://polymarket.com/event/" + (market.get("slug") or event.get("slug", ""))),
            })
    if not candidates:
        raise RuntimeError("No active BTC 5-minute market found in Polymarket's event feed.")
    candidates.sort(key=lambda x: x["end"])
    return candidates[0]


@st.cache_data(ttl=3, show_spinner=False)
def get_book(token_id):
    return get_json(f"{CLOB}/book", {"token_id": token_id})


def book_quotes(book):
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    bid = max((float(x["price"]) for x in bids), default=None)
    ask = min((float(x["price"]) for x in asks), default=None)
    mid = (bid + ask) / 2 if bid is not None and ask is not None else (ask if ask is not None else bid)
    return bid, ask, mid


def rsi(close, period=14):
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    return (100 - 100 / (1 + rs)).fillna(50)


def preliminary_estimate(df, up_mid, down_mid):
    close = df["close"]
    r1 = float(close.pct_change(1).iloc[-1])
    r3 = float(close.pct_change(3).iloc[-1])
    r5 = float(close.pct_change(5).iloc[-1])
    rsi_value = float(rsi(close).iloc[-1])
    momentum = (0.25 * math.tanh(r1 * 10000)
                + 0.30 * math.tanh(r3 * 5000)
                + 0.35 * math.tanh(r5 * 3500))
    rsi_component = max(-1.0, min(1.0, (rsi_value - 50) / 20))
    raw_score = 1.8 * momentum + 0.30 * rsi_component
    raw_up = 1 / (1 + math.exp(-max(-2, min(2, raw_score))))
    # Market-aware blend is only a heuristic. Not a calibrated probability.
    if up_mid is not None and down_mid is not None and up_mid + down_mid > 0:
        market_up = up_mid / (up_mid + down_mid)
        blended = 0.65 * raw_up + 0.35 * market_up
    else:
        market_up = None
        blended = raw_up
    return {
        "raw_up": raw_up, "estimate_up": blended, "estimate_down": 1 - blended,
        "market_up": market_up, "rsi": rsi_value, "r1": r1, "r3": r3, "r5": r5,
    }


try:
    with st.spinner("Loading BTC and Polymarket market data…"):
        btc_price, candles = get_btc()
        market = get_current_market()
        up_book = get_book(market["up_token"])
        down_book = get_book(market["down_token"])
        up_bid, up_ask, up_mid = book_quotes(up_book)
        down_bid, down_ask, down_mid = book_quotes(down_book)
        estimate = preliminary_estimate(candles, up_mid, down_mid)

    now = datetime.now(timezone.utc)
    seconds_left = max(0, int((market["end"] - now).total_seconds()))
    mm, ss = divmod(seconds_left, 60)

    st.caption("Last update: " + now.strftime("%Y-%m-%d %H:%M:%S UTC"))
    st.subheader(market["question"])
    top1, top2, top3 = st.columns(3)
    top1.metric("BTC / USDT", f"${btc_price:,.2f}")
    top2.metric("Time left in market", f"{mm}:{ss:02d}")
    top3.metric("Market status", "LIVE" if seconds_left > 0 else "ENDING")
    st.markdown(f"[Open this market on Polymarket ↗]({market['url']})")

    st.divider()
    st.subheader("Contract prices")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### 🟢 UP")
        st.metric("Best bid", f"{up_bid:.3f}" if up_bid is not None else "Unavailable")
        st.metric("Best ask (buy price)", f"{up_ask:.3f}" if up_ask is not None else "Unavailable")
        if up_bid is not None and up_ask is not None:
            st.caption(f"Spread: {(up_ask-up_bid)*100:.2f}¢ per share")
    with c2:
        st.markdown("### 🔴 DOWN")
        st.metric("Best bid", f"{down_bid:.3f}" if down_bid is not None else "Unavailable")
        st.metric("Best ask (buy price)", f"{down_ask:.3f}" if down_ask is not None else "Unavailable")
        if down_bid is not None and down_ask is not None:
            st.caption(f"Spread: {(down_ask-down_bid)*100:.2f}¢ per share")

    st.divider()
    st.subheader("Experimental signal — not yet validated")
    p_up = estimate["estimate_up"]
    p_down = estimate["estimate_down"]
    if up_ask is None or down_ask is None or seconds_left <= 0:
        signal = "NO TRADE"
        reason = "Market ask prices or active market timing are unavailable."
    elif p_up >= threshold and (estimate["market_up"] is None or p_up > estimate["market_up"] + 0.03):
        signal = "UP WATCH"
        reason = "Preliminary estimate is above the configured threshold and above the market-implied estimate."
    elif p_down >= threshold and (estimate["market_up"] is None or p_down > (1-estimate["market_up"]) + 0.03):
        signal = "DOWN WATCH"
        reason = "Preliminary estimate is above the configured threshold and above the market-implied estimate."
    else:
        signal = "NO TRADE"
        reason = "No sufficiently clear difference between this heuristic and the market-implied price."
    st.markdown(f"## {signal}")
    m1, m2, m3 = st.columns(3)
    m1.metric("Heuristic UP estimate", f"{p_up:.1%}")
    m2.metric("Heuristic DOWN estimate", f"{p_down:.1%}")
    m3.metric("Market-implied UP (midpoint)", f"{estimate['market_up']:.1%}" if estimate["market_up"] is not None else "Unavailable")
    st.info(reason)
    st.warning(
        "This is a research prototype, not a trained or calibrated model. The estimate is a hand-built "
        "heuristic blended with contract prices; it may be wrong and does not establish a profitable edge. "
        "It does not place trades. Consider fees, spread, slippage, resolution rules, and the time remaining."
    )

    st.subheader("BTC price movement")
    chart = candles.set_index("open_time")[["close"]].rename(columns={"close": "BTC close"})
    st.line_chart(chart.tail(60), height=260)
    f1, f2, f3 = st.columns(3)
    f1.metric("RSI (14)", f"{estimate['rsi']:.1f}")
    f2.metric("1-minute return", f"{estimate['r1']*100:.3f}%")
    f3.metric("5-minute return", f"{estimate['r5']*100:.3f}%")
    with st.expander("Why this is not a real edge yet"):
        st.write(
            "The displayed estimates come from a hand-built formula, not a trained model. "
            "To evaluate it, we need to log predictions with timestamps, compare them with the "
            "settlement outcome, and test on unseen periods. A useful strategy must beat the contract "
            "price after fees and execution costs, not merely guess BTC direction."
        )
except requests.HTTPError as exc:
    status = exc.response.status_code if exc.response is not None else "unknown"
    st.error(f"Market data request failed (HTTP {status}).")
    st.write("Try refreshing in a moment. Public APIs can be rate-limited or temporarily unavailable.")
except Exception as exc:
    st.error("Couldn't load the current Polymarket BTC 5-minute market.")
    st.write(str(exc))
    st.caption("If the market discovery API changes its response, we may need to update the market selector.")
