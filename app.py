import json
import math
import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="BTC 5m Market Monitor", page_icon="₿", layout="wide")

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
BINANCE = "https://data-api.binance.vision"
HEADERS = {"User-Agent": "BTC-5m-Market-Monitor/1.0"}

st.title("₿ Polymarket BTC 5-Minute Monitor")
st.caption("Read-only market monitor • automatic snapshots • manual trading only")

with st.sidebar:
    st.header("Monitor settings")
    refresh_seconds = st.select_slider("Refresh interval", options=[10, 15, 20, 30, 45, 60], value=15)
    momentum_seconds = st.select_slider("BTC momentum window", options=[30, 60, 90, 120, 180], value=60)
    alert_threshold = st.slider("Alert when model probability differs from market by", 1, 20, 7, format="%d percentage points")
    st.caption("The monitor never places orders. Keep this page open to collect snapshots.")
    st.divider()
    st.markdown("**Cloud history**")
    st.write("Supabase connection:", "Connected" if all(k in st.secrets for k in ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]) else "Not configured")
    st.caption("Without Supabase, this session can display live data but historical records won't survive restarts.")
    st.divider()
    st.markdown("**Optional Telegram alerts**")
    telegram_ready = all(k in st.secrets for k in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"])
    st.write("Telegram:", "Configured" if telegram_ready else "Not configured")
    st.caption("Optional: add a Telegram bot token and chat ID in Streamlit Secrets to receive a message when the watch signal changes.")

st_autorefresh(interval=refresh_seconds * 1000, key="refresh")

def get_json(url, params=None, timeout=12):
    r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()

def parse_json_field(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return []
    return []

def market_text(m):
    return " ".join(str(m.get(k, "")) for k in ["question", "title", "slug", "description"]).lower()

def is_target_market(m):
    txt = market_text(m)
    # Prefer clearly labeled BTC/Bitcoin Up-or-Down five-minute markets.
    crypto = ("bitcoin" in txt) or ("btc" in txt)
    updown = ("up or down" in txt) or ("up-or-down" in txt) or ("up/down" in txt)
    five_min = ("5m" in txt) or ("5-min" in txt) or ("5 min" in txt) or ("5 minute" in txt) or ("5-minute" in txt)
    return crypto and updown and five_min and not m.get("closed", False)

@st.cache_data(ttl=8, show_spinner=False)
def discover_market():
    candidates = []
    # Gamma event results often contain the market objects directly.
    for endpoint, params in [
        (f"{GAMMA}/events", {"active": "true", "closed": "false", "limit": 100}),
        (f"{GAMMA}/markets", {"active": "true", "closed": "false", "limit": 200}),
    ]:
        try:
            data = get_json(endpoint, params)
            if isinstance(data, dict):
                data = data.get("events", data.get("markets", []))
            for item in data or []:
                if isinstance(item, dict) and isinstance(item.get("markets"), list):
                    candidates.extend(item["markets"])
                elif isinstance(item, dict):
                    candidates.append(item)
        except Exception:
            continue
    # Deduplicate by market id/slug.
    unique = {}
    for m in candidates:
        unique[str(m.get("id") or m.get("slug") or id(m))] = m
    matches = [m for m in unique.values() if is_target_market(m)]
    if not matches:
        return None, "Couldn't confidently identify an active BTC 5-minute Up/Down market. Check that Polymarket currently lists one, then refresh."
    now = datetime.now(timezone.utc)
    def end_sort(m):
        raw = m.get("endDate") or m.get("end_date_iso") or ""
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return now
    future = [m for m in matches if end_sort(m) >= now]
    pool = future or matches
    pool.sort(key=end_sort)
    return pool[0], None

def token_map(m):
    outcomes = parse_json_field(m.get("outcomes"))
    token_ids = parse_json_field(m.get("clobTokenIds") or m.get("clob_token_ids"))
    if not outcomes and isinstance(m.get("tokens"), list):
        outcomes = [x.get("outcome") for x in m["tokens"]]
        token_ids = [x.get("token_id") or x.get("tokenId") for x in m["tokens"]]
    out = {}
    for outcome, token in zip(outcomes, token_ids):
        label = str(outcome).strip().lower()
        if label in ("up", "yes", "higher"):
            out["UP"] = str(token)
        elif label in ("down", "no", "lower"):
            out["DOWN"] = str(token)
    return out

@st.cache_data(ttl=3, show_spinner=False)
def get_book(token_id):
    return get_json(f"{CLOB}/book", {"token_id": token_id})

def best_prices(book):
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    bid_vals = [float(x["price"]) for x in bids if x.get("price") is not None]
    ask_vals = [float(x["price"]) for x in asks if x.get("price") is not None]
    # Use numeric max/min rather than assuming API list ordering.
    bid = max(bid_vals) if bid_vals else None
    ask = min(ask_vals) if ask_vals else None
    bid_size = sum(float(x.get("size", 0)) for x in bids if bid is not None and float(x.get("price", -1)) == bid)
    ask_size = sum(float(x.get("size", 0)) for x in asks if ask is not None and float(x.get("price", -1)) == ask)
    return bid, ask, bid_size, ask_size

@st.cache_data(ttl=2, show_spinner=False)
def btc_prices():
    spot = get_json(f"{BINANCE}/api/v3/ticker/price", {"symbol": "BTCUSDT"})
    klines = get_json(f"{BINANCE}/api/v3/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": 5})
    current = float(spot["price"])
    closes = [float(k[4]) for k in klines]
    previous = closes[-2] if len(closes) >= 2 else current
    older = closes[-2] if len(closes) >= 2 else current
    older2 = closes[-3] if len(closes) >= 3 else older
    return current, previous, older2

def supabase_configured():
    return "SUPABASE_URL" in st.secrets and "SUPABASE_SERVICE_ROLE_KEY" in st.secrets

def save_snapshot(row):
    if not supabase_configured():
        return False, "Supabase not configured"
    url = str(st.secrets["SUPABASE_URL"]).rstrip("/") + "/rest/v1/market_snapshots"
    key = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        r = requests.post(url, headers=headers, json=row, timeout=10)
        if r.status_code >= 300:
            return False, f"Supabase insert error {r.status_code}: {r.text[:180]}"
        return True, "Saved"
    except Exception as e:
        return False, str(e)

def read_recent():
    if not supabase_configured():
        return pd.DataFrame()
    url = str(st.secrets["SUPABASE_URL"]).rstrip("/") + "/rest/v1/market_snapshots"
    key = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    try:
        r = requests.get(url, headers=headers, params={"select":"created_at,market_question,btc_price,btc_change_pct,up_bid,up_ask,down_bid,down_ask,signal,edge_pp,outcome", "order":"created_at.desc", "limit":"300"}, timeout=10)
        r.raise_for_status()
        return pd.DataFrame(r.json())
    except Exception:
        return pd.DataFrame()

market, discovery_error = discover_market()
if discovery_error:
    st.warning(discovery_error)
    st.info("This can happen if the market slug/title changed or the market is temporarily unavailable. The app deliberately avoids guessing at a market.")
    st.stop()

mapping = token_map(market)
if "UP" not in mapping or "DOWN" not in mapping:
    st.error("Found a possible market, but couldn't safely map its outcome tokens to UP and DOWN. No signal will be generated.")
    st.write("Market metadata:", {k: market.get(k) for k in ["question", "slug", "outcomes", "clobTokenIds", "endDate"]})
    st.stop()

try:
    up_book = get_book(mapping["UP"])
    down_book = get_book(mapping["DOWN"])
    btc, btc_prev, btc_older = btc_prices()
except Exception as e:
    st.error(f"Live data request failed: {e}")
    st.caption("This is a data/API failure, not a trading signal. The page will retry on its next refresh.")
    st.stop()

up_bid, up_ask, up_bid_size, up_ask_size = best_prices(up_book)
down_bid, down_ask, down_bid_size, down_ask_size = best_prices(down_book)
up_mid = (up_bid + up_ask) / 2 if up_bid is not None and up_ask is not None else None
down_mid = (down_bid + down_ask) / 2 if down_bid is not None and down_ask is not None else None
now = datetime.now(timezone.utc)
end_raw = market.get("endDate") or market.get("end_date_iso")
try:
    end_dt = datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
    seconds_left = max(0, int((end_dt - now).total_seconds()))
except Exception:
    seconds_left = None
btc_change = ((btc - btc_older) / btc_older * 100) if btc_older else 0.0
btc_change_short = ((btc - btc_prev) / btc_prev * 100) if btc_prev else 0.0

# Transparent heuristic only; not a trained or validated model.
up_market_prob = up_mid if up_mid is not None else 0.5
# Momentum nudges the market-implied probability modestly; this is NOT calibrated.
momentum_nudge = max(-0.08, min(0.08, btc_change * 0.12))
model_up = max(0.02, min(0.98, up_market_prob + momentum_nudge))
edge_pp = (model_up - up_market_prob) * 100
if abs(edge_pp) >= alert_threshold:
    signal = "UP WATCH" if edge_pp > 0 else "DOWN WATCH"
else:
    signal = "NO CLEAR EDGE"

c1, c2, c3, c4 = st.columns(4)
c1.metric("BTC / USDT spot", f"${btc:,.2f}", f"{btc_change:+.3f}% over ~3 min")
c2.metric("UP midpoint", f"{up_mid:.3f}" if up_mid is not None else "—", f"Bid {up_bid:.3f} / Ask {up_ask:.3f}" if up_bid is not None and up_ask is not None else "No book")
c3.metric("DOWN midpoint", f"{down_mid:.3f}" if down_mid is not None else "—", f"Bid {down_bid:.3f} / Ask {down_ask:.3f}" if down_bid is not None and down_ask is not None else "No book")
c4.metric("Time remaining", f"{seconds_left//60}:{seconds_left%60:02d}" if seconds_left is not None else "Unknown")

st.subheader("Current market")
st.write(f"**{market.get('question') or market.get('title') or market.get('slug')}**")
if market.get("slug"):
    st.markdown(f"[Open this market on Polymarket](https://polymarket.com/event/{market.get('slug')})")
st.caption("Important: BTC/USDT from Binance is a comparison feed, not necessarily the official resolution oracle. Read the market's rules before acting.")

st.subheader("Signal monitor")
s1, s2, s3 = st.columns(3)
s1.metric("Heuristic model UP estimate", f"{model_up*100:.1f}%")
s2.metric("Market-implied UP midpoint", f"{up_market_prob*100:.1f}%")
s3.metric("Model minus market", f"{edge_pp:+.1f} pp")
if signal == "NO CLEAR EDGE":
    st.info(f"**{signal}.** No alert threshold crossed. This simple momentum nudge is not a proven trading edge.")
else:
    st.warning(f"**{signal}.** The simple heuristic crossed your alert threshold. This is a watch alert, not a recommendation or guarantee.")

# Optional Telegram notification when the alert state changes, rather than on every refresh.
if signal != "NO CLEAR EDGE" and all(k in st.secrets for k in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]):
    alert_key = f"{market.get('id') or market.get('slug')}:{signal}"
    if st.session_state.get("last_telegram_alert") != alert_key:
        try:
            telegram_url = f"https://api.telegram.org/bot{st.secrets['TELEGRAM_BOT_TOKEN']}/sendMessage"
            telegram_text = (
                f"BTC 5m watch alert: {signal}\\n"
                f"Market: {market.get('question') or market.get('title') or market.get('slug')}\\n"
                f"BTC/USDT: ${btc:,.2f} ({btc_change:+.3f}% over ~3 min)\\n"
                f"UP midpoint: {up_mid if up_mid is not None else 'n/a'}\\n"
                f"DOWN midpoint: {down_mid if down_mid is not None else 'n/a'}\\n"
                f"Model-market difference: {edge_pp:+.1f} percentage points\\n"
                "Watch alert only; not a validated forecast. Verify official market rules."
            )
            tr = requests.post(telegram_url, json={"chat_id": st.secrets["TELEGRAM_CHAT_ID"], "text": telegram_text}, timeout=8)
            tr.raise_for_status()
            st.session_state["last_telegram_alert"] = alert_key
            st.caption("Telegram watch alert sent.")
        except Exception as e:
            st.caption(f"Telegram notification failed: {e}")

st.caption("Model limitations: the estimate is a transparent demo heuristic, not a trained/validated probability. Do not treat it as a 5-minute outcome forecast until the tracker has enough clean labeled data and out-of-sample tests.")

snapshot = {
    "market_id": str(market.get("id") or market.get("conditionId") or market.get("slug") or "unknown"),
    "market_slug": str(market.get("slug") or ""),
    "market_question": str(market.get("question") or market.get("title") or ""),
    "market_end_time": end_dt.isoformat() if end_raw and 'end_dt' in locals() else None,
    "created_at": now.isoformat(),
    "btc_price": btc,
    "btc_change_pct": btc_change,
    "btc_change_short_pct": btc_change_short,
    "up_bid": up_bid,
    "up_ask": up_ask,
    "up_mid": up_mid,
    "up_bid_size": up_bid_size,
    "up_ask_size": up_ask_size,
    "down_bid": down_bid,
    "down_ask": down_ask,
    "down_mid": down_mid,
    "down_bid_size": down_bid_size,
    "down_ask_size": down_ask_size,
    "model_up_prob": model_up,
    "edge_pp": edge_pp,
    "signal": signal,
    "seconds_remaining": seconds_left,
    "outcome": None,
}
# Avoid inserting duplicates from Streamlit reruns too frequently: only save once per market per refresh slot.
slot = int(time.time() // refresh_seconds)
dedupe_key = f"{market.get('id') or market.get('slug')}:{slot}"
if st.session_state.get("last_saved_key") != dedupe_key:
    ok, msg = save_snapshot(snapshot)
    st.session_state["last_saved_key"] = dedupe_key
    if ok:
        st.caption("Cloud snapshot saved.")
    elif supabase_configured():
        st.caption(f"Cloud save failed: {msg}")
    else:
        st.caption("Live view is working; configure Supabase to keep a durable history.")

st.divider()
st.subheader("Historical snapshots")
history = read_recent()
if not history.empty:
    st.caption(f"Showing up to {len(history)} most recent cloud snapshots. Outcomes are not automatically labeled by this version; use the market's final official resolution for evaluation.")
    display_cols = [c for c in ["created_at","market_question","btc_price","btc_change_pct","up_bid","up_ask","down_bid","down_ask","signal","edge_pp","outcome"] if c in history.columns]
    st.dataframe(history[display_cols], use_container_width=True, hide_index=True)
    if "signal" in history.columns:
        counts = history["signal"].value_counts().rename_axis("Signal").reset_index(name="Snapshots")
        st.bar_chart(counts.set_index("Signal"))
else:
    st.info("No cloud history found yet. Connect Supabase using the included setup guide, then keep the dashboard open.")

with st.expander("Set up alerts"):
    st.write("Dashboard alerts work immediately. Telegram alerts are optional and send when the signal changes to UP WATCH or DOWN WATCH.")
    st.write("To set up Telegram: create a bot with @BotFather, start a chat with your bot, find your chat ID using Telegram's bot API, then add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to Streamlit Cloud Secrets. Never put the token in GitHub.")
    st.write("For dependable 24/7 monitoring even when nobody has the dashboard open, a separate always-on worker is still needed. This Streamlit version collects snapshots while the app is running and being visited.")
with st.expander("How to read the order book"):
    st.write("Best bid = highest price a buyer is currently offering. Best ask = lowest price a seller is currently asking. The difference is the spread. Large size at the best quote can disappear quickly; snapshots do not guarantee you could fill there.")
