import math
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="BTC Signal Lab", page_icon="₿", layout="centered")

# Binance's public market-data-only host can work when the main API host is blocked.
BINANCE_URL = "https://data-api.binance.vision/api/v3/klines"
TICKER_URL = "https://data-api.binance.vision/api/v3/ticker/price"

st.markdown("""
<style>
.block-container {max-width: 900px; padding-top: 1.6rem; padding-bottom: 2rem;}
div[data-testid="stMetric"] {background: rgba(128,128,128,.08); padding: 14px; border-radius: 12px;}
</style>
""", unsafe_allow_html=True)

st.title("₿ BTC Signal Lab")
st.caption("Bitcoin 5-minute direction dashboard · educational prototype · no trades placed")

with st.sidebar:
    st.header("Settings")
    refresh_seconds = st.selectbox("Refresh interval", [15, 30, 60], index=1)
    threshold = st.slider("Signal strength threshold", 0.52, 0.70, 0.58, 0.01)
    st.caption("The threshold controls when the prototype displays UP or DOWN instead of NO TRADE.")
    st.divider()
    st.markdown("**Data source:** Binance public spot market data (BTC/USDT)")
    st.markdown("No API key or exchange account is needed.")

st_autorefresh(interval=refresh_seconds * 1000, key="btc_refresh")


@st.cache_data(ttl=10, show_spinner=False)
def get_market_data():
    price_resp = requests.get(TICKER_URL, params={"symbol": "BTCUSDT"}, timeout=10)
    price_resp.raise_for_status()
    price = float(price_resp.json()["price"])

    candles_resp = requests.get(
        BINANCE_URL,
        params={"symbol": "BTCUSDT", "interval": "1m", "limit": 120},
        timeout=10,
    )
    candles_resp.raise_for_status()
    raw = candles_resp.json()
    columns = [
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"
    ]
    df = pd.DataFrame(raw, columns=columns)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return price, df


def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def make_signal(df):
    close = df["close"]
    volume = df["volume"]
    ret_1 = close.pct_change(1).iloc[-1]
    ret_3 = close.pct_change(3).iloc[-1]
    ret_5 = close.pct_change(5).iloc[-1]
    rsi = calc_rsi(close).iloc[-1]

    # Transparent hand-built baseline, not a trained or calibrated ML model.
    # Features are clipped so a single extreme candle doesn't dominate.
    momentum = (
        0.25 * math.tanh(float(ret_1) * 10000)
        + 0.30 * math.tanh(float(ret_3) * 5000)
        + 0.35 * math.tanh(float(ret_5) * 3500)
    )
    rsi_component = max(-1.0, min(1.0, (float(rsi) - 50.0) / 20.0))
    vol_avg = volume.rolling(20).mean().iloc[-1]
    vol_ratio = float(volume.iloc[-1] / vol_avg) if vol_avg and vol_avg > 0 else 1.0
    volume_component = max(0.0, min(1.5, vol_ratio - 1.0))
    # Volume only modestly strengthens the existing momentum direction.
    score = 1.8 * momentum + 0.30 * rsi_component
    score *= 1.0 + 0.10 * volume_component
    score = max(-2.0, min(2.0, score))
    p_up = 1.0 / (1.0 + math.exp(-score))
    p_down = 1.0 - p_up

    if p_up >= threshold:
        signal = "UP"
    elif p_down >= threshold:
        signal = "DOWN"
    else:
        signal = "NO TRADE"

    return {
        "signal": signal, "p_up": p_up, "p_down": p_down,
        "rsi": float(rsi), "ret_1": float(ret_1),
        "ret_3": float(ret_3), "ret_5": float(ret_5),
        "vol_ratio": vol_ratio,
    }


try:
    with st.spinner("Loading latest BTC market data…"):
        price, df = get_market_data()
    signal = make_signal(df)
    st.caption("Last refresh: " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))

    st.metric("BTC / USDT spot price", f"${price:,.2f}")
    st.subheader("Next 5-minute direction estimate")

    signal_color = {"UP": "🟢", "DOWN": "🔴", "NO TRADE": "🟡"}[signal["signal"]]
    st.markdown(f"## {signal_color} {signal['signal']}")
    c1, c2 = st.columns(2)
    c1.metric("UP score", f"{signal['p_up']:.1%}")
    c2.metric("DOWN score", f"{signal['p_down']:.1%}")
    st.progress(signal["p_up"], text="UP score (the DOWN score is its complement)")

    st.warning(
        "Important: these percentages are outputs of a simple hand-built scoring formula. "
        "They are NOT statistically calibrated probabilities and this version has NOT been "
        "trained or validated for profitable predictions. Treat signals as experimental only."
    )

    chart_df = df.set_index("open_time")[["close"]].rename(columns={"close": "BTC close"})
    st.subheader("Recent 1-minute candles")
    st.line_chart(chart_df.tail(60), height=240)

    st.subheader("Signals behind the estimate")
    a, b, c = st.columns(3)
    a.metric("RSI (14)", f"{signal['rsi']:.1f}")
    b.metric("1-minute return", f"{signal['ret_1'] * 100:.3f}%")
    c.metric("5-minute return", f"{signal['ret_5'] * 100:.3f}%")

    st.caption(
        f"Volume vs. recent average: {signal['vol_ratio']:.2f}× · "
        "Market data can be delayed or temporarily unavailable. This dashboard does not execute trades."
    )
    with st.expander("How this prototype works"):
        st.write(
            "It combines short-term returns, RSI, and a modest volume adjustment into a score. "
            "A logistic function converts the score into two numbers that sum to 100%. "
            "That conversion does not make the numbers calibrated probabilities. The next build "
            "should use historical candles, time-ordered out-of-sample testing, and calibration."
        )
except requests.HTTPError as exc:
    status = exc.response.status_code if exc.response is not None else "unknown"
    st.error("Could not load BTC market data right now.")
    st.write(
        "The data provider rejected the request. This updated version uses Binance's "
        "public market-data-only endpoint. If it still fails, wait briefly and check the app logs."
    )
    st.caption(f"HTTP status: {status}")
except Exception as exc:
    st.error("Could not load BTC market data right now.")
    st.write("Wait a little and refresh the page. The public data endpoint may be rate-limited or temporarily unavailable.")
    st.caption(f"Technical detail: {type(exc).__name__}")
