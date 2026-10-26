-- Run this once in your Supabase project's SQL Editor.
create table if not exists public.market_snapshots (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  market_id text not null,
  market_slug text,
  market_question text,
  market_end_time timestamptz,
  btc_price double precision,
  btc_change_pct double precision,
  btc_change_short_pct double precision,
  up_bid double precision,
  up_ask double precision,
  up_mid double precision,
  up_bid_size double precision,
  up_ask_size double precision,
  down_bid double precision,
  down_ask double precision,
  down_mid double precision,
  down_bid_size double precision,
  down_ask_size double precision,
  model_up_prob double precision,
  edge_pp double precision,
  signal text,
  seconds_remaining integer,
  outcome text
);

create index if not exists market_snapshots_created_at_idx
  on public.market_snapshots (created_at desc);
create index if not exists market_snapshots_market_id_idx
  on public.market_snapshots (market_id);

-- The app uses the Supabase service-role key server-side, stored only in Streamlit secrets.
-- Do not expose that key in the browser, screenshots, public GitHub files, or chat.
