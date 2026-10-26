# Polymarket BTC 5-Minute Market Monitor

A read-only Streamlit dashboard that:
- Finds a likely active BTC/Bitcoin 5-minute Up/Down market using public Gamma metadata.
- Reads both outcome order books from the public Polymarket CLOB.
- Reads BTC/USDT spot and short-term movement from Binance public market data.
- Refreshes automatically while the dashboard is open.
- Saves timestamped snapshots to Supabase when configured.
- Displays in-dashboard watch alerts and can optionally send Telegram messages when the signal changes; it never places trades.

## Important limitations
- The current signal is a transparent demonstration heuristic, not a trained or validated predictor. It is not evidence of a profitable edge.
- Binance BTC/USDT is not necessarily the market's official resolution reference. Read the rules on the specific Polymarket market before acting.
- The app deliberately refuses to guess if it cannot confidently map the market/outcome tokens.
- This version does not automatically label resolved outcomes or send push/email notifications. Optional Telegram alerts are supported through Streamlit Secrets.
- A Streamlit app is not a guaranteed 24/7 background worker. Snapshot collection happens while the app is running and visited. For continuous monitoring when nobody has the dashboard open, add a separate always-on worker.
- Public endpoints may change, rate-limit, or be unavailable.

## Deploy to Streamlit Community Cloud

1. Download and unzip this project.
2. Create a GitHub repository (public is easiest for this beginner setup).
3. Upload `app.py`, `requirements.txt`, and `supabase_schema.sql` to the repository root. Do not upload secrets.
4. Visit https://share.streamlit.io/ and deploy the repository, choosing `app.py` as the main file.
5. The dashboard should run in read-only mode even before Supabase is configured.

## Add permanent cloud history with Supabase

1. Create a project at https://supabase.com/.
2. In Supabase, open **SQL Editor**, create a query, paste the contents of `supabase_schema.sql`, and run it.
3. In Supabase project settings, copy the Project URL and the **service_role** API key. Treat the service-role key like a password.
4. In Streamlit Cloud, open your app's **Settings → Secrets** and add:

```toml
SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "YOUR_SERVICE_ROLE_KEY"
```

5. Save the secrets and reboot the app. The dashboard should show cloud snapshots being saved.

## Optional Telegram alerts

1. In Telegram, message `@BotFather`, create a bot, and copy the bot token.
2. Open a chat with your new bot and press Start.
3. Retrieve your chat ID using Telegram's `getUpdates` API after sending your bot a message, or use a trusted Telegram chat-ID helper bot.
4. Add these to Streamlit Cloud **Settings → Secrets** (alongside the Supabase values):

```toml
TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"
```

5. Save and reboot. Telegram messages are sent when the dashboard detects a change into an UP WATCH or DOWN WATCH state. This is an alert, not an order or a validated forecast.

Security note: the service-role key bypasses normal row-level security. Keep it only in Streamlit's server-side Secrets settings. Never commit it to GitHub or share it publicly.

## Local Mac run (optional)

Install Python 3.10+ then, in Terminal, from the project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## What the dashboard records

Each snapshot stores UTC timestamp, market identifier and question, market end time, BTC spot/momentum, UP/DOWN bid/ask/midpoint, best-quote sizes, heuristic estimate, difference from market midpoint, signal label, and seconds remaining. Outcomes are left blank intentionally until they can be labeled from the official resolution source.

## Before trusting any strategy

Collect data across many markets and days. Evaluate only on markets not used to invent/tune a strategy. Include executable entry price, spread, fees, slippage, and market-resolution rules. A high apparent hit rate on a small sample is not enough.
