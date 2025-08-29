# app.py — Single-file Streamlit Paper Trading App
# Features:
# - Simulated price feed (random-walk)
# - Buy/Sell with balance, PnL, and transaction history (SQLite)
# - NSE Option Chain fetch with headers + cookie warm-up, cached
# - Per-user via User ID field (persists in SQLite); session auto-ID by default
# - Minimal deps: streamlit, requests (sqlite3 in stdlib)

import os
import time
import uuid
import json
import random
import sqlite3
import threading
from datetime import datetime
import requests
import streamlit as st

# ----------------------------
# Config (override via Streamlit Secrets or Env)
# ----------------------------
STARTING_BALANCE = float(os.getenv("STARTING_BALANCE", "100000"))  # e.g., 0 to mirror your screenshot
SIM_START_PRICE = float(os.getenv("SIM_START_PRICE", "997.28"))
SIM_TICK_MS = int(os.getenv("SIM_TICK_MS", "1500"))
NSE_CACHE_TTL = int(os.getenv("NSE_CACHE_TTL", "15"))  # seconds
DB_PATH = os.getenv("DB_PATH", "paper_trading.db")

st.set_page_config(page_title="Paper Trading App", layout="wide")

# ----------------------------
# DB: cached connection + init
# ----------------------------
db_lock = threading.Lock()

@st.cache_resource
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                balance REAL NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
                qty INTEGER NOT NULL CHECK (qty > 0),
                price REAL NOT NULL,
                ts INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id)")
    return conn

conn = get_db()

def ensure_user(uid: str, starting_balance: float = STARTING_BALANCE):
    with db_lock:
        r = conn.execute("SELECT user_id FROM users WHERE user_id=?", (uid,)).fetchone()
        if r is None:
            conn.execute(
                "INSERT INTO users (user_id, balance, created_at) VALUES (?,?,?)",
                (uid, float(starting_balance), int(time.time()))
            )
            conn.commit()

def get_balance(uid: str) -> float:
    r = conn.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
    return float(r["balance"]) if r else 0.0

def get_net_qty(uid: str) -> int:
    r = conn.execute("""
        SELECT COALESCE(SUM(CASE WHEN side='BUY' THEN qty ELSE -qty END),0) AS net_qty
        FROM trades WHERE user_id=?
    """, (uid,)).fetchone()
    return int(r["net_qty"] if r and r["net_qty"] is not None else 0)

def insert_trade(uid: str, side: str, qty: int, price: float):
    with db_lock:
        conn.execute(
            "INSERT INTO trades (user_id, side, qty, price, ts) VALUES (?,?,?,?,?)",
            (uid, side, qty, price, int(time.time()))
        )
        conn.commit()

def update_balance(uid: str, new_balance: float):
    with db_lock:
        conn.execute("UPDATE users SET balance=? WHERE user_id=?", (float(new_balance), uid))
        conn.commit()

def reset_account(uid: str, starting_balance: float = STARTING_BALANCE):
    with db_lock:
        conn.execute("DELETE FROM trades WHERE user_id=?", (uid,))
        conn.execute("UPDATE users SET balance=? WHERE user_id=?", (float(starting_balance), uid))
        conn.commit()

def fetch_history(uid: str, limit: int = 200):
    rows = conn.execute("""
        SELECT id, side, qty, price, ts FROM trades
        WHERE user_id=? ORDER BY id DESC LIMIT ?
    """, (uid, limit)).fetchall()
    return [dict(r) for r in rows]

# ----------------------------
# Simulated price (session state)
# ----------------------------
def step_price():
    now_ms = int(time.time() * 1000)
    last_price = st.session_state.get("last_price", SIM_START_PRICE)
    last_ts = st.session_state.get("last_ts", now_ms)

    if now_ms - last_ts >= SIM_TICK_MS:
        p = last_price
        # mild mean reversion + small shock
        drift = -0.02 * (p - SIM_START_PRICE) / SIM_START_PRICE
        shock = random.uniform(-0.20, 0.20)  # smooth
        new_p = max(1.0, round(p * (1 + drift*0.01 + shock*0.001), 2))
        st.session_state.last_price = new_p
        st.session_state.last_ts = now_ms
    else:
        # keep same price if tick not elapsed
        st.session_state.last_price = last_price
        st.session_state.last_ts = last_ts

    # append for chart
    if "price_series" not in st.session_state:
        st.session_state.price_series = []
    st.session_state.price_series.append((now_ms, st.session_state.last_price))
    if len(st.session_state.price_series) > 120:
        st.session_state.price_series = st.session_state.price_series[-120:]

    return st.session_state.last_price

# ----------------------------
# NSE Option Chain with cache
# ----------------------------
@st.cache_data(ttl=NSE_CACHE_TTL)
def get_option_chain(symbol: str = "NIFTY", is_index: bool = True):
    base_url = "https://www.nseindia.com"
    api_path = f"/api/option-chain-{'indices' if is_index else 'equities'}?symbol={symbol.upper()}"
    api_url = base_url + api_path
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/option-chain",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }
    s = requests.Session()
    s.get(base_url + "/option-chain", headers=headers, timeout=10)
    r = s.get(api_url, headers=headers, timeout=10)
    r.raise_for_status()
    raw = r.json()

    expiry_dates = raw.get("records", {}).get("expiryDates", [])
    if not expiry_dates:
        return {"symbol": symbol.upper(), "expiry": None, "strikes": []}

    current_expiry = expiry_dates[0]
    strikes = []
    for item in raw.get("records", {}).get("data", []):
        if item.get("expiryDate") == current_expiry:
            sp = item.get("strikePrice")
            ce = item.get("CE") or {}
            pe = item.get("PE") or {}
            ce_oi = ce.get("openInterest", 0) or 0
            pe_oi = pe.get("openInterest", 0) or 0
            strikes.append({"strike": sp, "ce_oi": ce_oi, "pe_oi": pe_oi})
    strikes.sort(key=lambda x: (x["strike"] is None, x["strike"]))
    return {"symbol": symbol.upper(), "expiry": current_expiry, "strikes": strikes}

def is_index_symbol(sym: str) -> bool:
    return sym.upper() in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}

# ----------------------------
# Helpers
# ----------------------------
def inr(x: float) -> str:
    try:
        return f"₹ {float(x):,.2f}"
    except Exception:
        return f"₹ {x}"

def portfolio(uid: str, last_price: float):
    qty = get_net_qty(uid)
    bal = get_balance(uid)
    pv = bal + qty * last_price
    return qty, bal, pv

# ----------------------------
# UI
# ----------------------------
st.title("Paper Trading App")

# Session user id default
if "uid" not in st.session_state:
    st.session_state.uid = str(uuid.uuid4())

with st.sidebar:
    st.markdown("### User & settings")
    uid_input = st.text_input("User ID", st.session_state.uid)
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Load/Create user", use_container_width=True):
            st.session_state.uid = uid_input.strip() or st.session_state.uid
            ensure_user(st.session_state.uid, STARTING_BALANCE)
            st.success(f"Active user: {st.session_state.uid}")
    with col_b:
        if st.button("Reset account", use_container_width=True):
            ensure_user(st.session_state.uid, STARTING_BALANCE)
            reset_account(st.session_state.uid, STARTING_BALANCE)
            # reset simulated price and series
            st.session_state.last_price = SIM_START_PRICE
            st.session_state.price_series = []
            st.toast("Account reset.")

    st.caption(f"Starting balance (env): {inr(STARTING_BALANCE)}")
    st.caption(f"Sim start price: {inr(SIM_START_PRICE)} | Tick: {SIM_TICK_MS} ms")

# Make sure user exists
ensure_user(st.session_state.uid, STARTING_BALANCE)

# Auto-refresh to advance price
count = st.experimental_memo.clear if False else None  # placeholder to avoid linter
st_autorefresh = st.experimental_rerun  # alias to avoid confusion in code reading
st.experimental_set_query_params(uid=st.session_state.uid)  # nice to have
st.runtime.legacy_caching.clear_cache = False  # no-op anchor

# Use Streamlit native autorefresh
st_autorefresh_count = st.sidebar.slider("Auto-refresh (ms)", 500, 5000, SIM_TICK_MS, 100)
st.sidebar.caption("Chart and price auto-update on interval.")
st.experimental_set_query_params(uid=st.session_state.uid, tick=st_autorefresh_count)
st_autorefresh_token = st.experimental_get_query_params()  # keep session stable
st_autorefresh = st.experimental_rerun  # not used; kept for readability

# Light autorefresh using empty container and timer
placeholder = st.empty()
with placeholder.container():
    last_price = step_price()

# KPIs
qty, bal, pv = portfolio(st.session_state.uid, last_price)
m1, m2, m3, m4 = st.columns(4)
m1.metric("User ID", st.session_state.uid[-12:])
m2.metric("Balance", inr(bal))
m3.metric("Shares held", f"{qty}")
m4.metric("Portfolio value", inr(pv))

# Price + chart
pc1, pc2 = st.columns([1, 3])
with pc1:
    st.subheader("Simulated price")
    st.metric("LTP", inr(last_price))
with pc2:
    st.subheader("Price chart")
    if "price_series" in st.session_state and st.session_state.price_series:
        times = [datetime.fromtimestamp(t/1000).strftime("%H:%M:%S") for t, _ in st.session_state.price_series]
        values = [p for _, p in st.session_state.price_series]
        st.line_chart({"Price": values}, height=180)
    else:
        st.info("Price stream starting…")

# Trade panel
st.subheader("Trade")
tc1, tc2, tc3, tc4 = st.columns([1, 1, 1, 3])
with tc1:
    qty_in = st.number_input("Quantity", min_value=1, max_value=1_000_000, value=1, step=1)
with tc2:
    buy = st.button("Buy", type="primary", use_container_width=True)
with tc3:
    sell = st.button("Sell", use_container_width=True)
trade_log = st.empty()

if buy or sell:
    side = "BUY" if buy else "SELL"
    price = step_price()
    current_bal = get_balance(st.session_state.uid)
    current_qty = get_net_qty(st.session_state.uid)
    cost = round(qty_in * price, 2)

    if side == "BUY":
        if current_bal < cost:
            trade_log.error("Insufficient balance")
        else:
            update_balance(st.session_state.uid, round(current_bal - cost, 2))
            insert_trade(st.session_state.uid, side, int(qty_in), float(price))
            trade_log.success(f"OK BUY {qty_in} @ {inr(price)}")
    else:
        if current_qty < qty_in:
            trade_log.error("Insufficient shares to sell")
        else:
            update_balance(st.session_state.uid, round(current_bal + cost, 2))
            insert_trade(st.session_state.uid, side, int(qty_in), float(price))
            trade_log.success(f"OK SELL {qty_in} @ {inr(price)}")

# History
st.subheader("Transaction history")
rows = fetch_history(st.session_state.uid, limit=200)
if rows:
    # Render as a lightweight table
    st.table([{
        "ID": r["id"],
        "Side": r["side"],
        "Qty": r["qty"],
        "Price": inr(r["price"]),
        "Time": datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d %H:%M:%S")
    } for r in rows])
else:
    st.caption("No trades yet.")

# NSE Option Chain
st.subheader("NSE Option Chain")
oc1, oc2, oc3 = st.columns([1, 1, 2])
with oc1:
    sym = st.text_input("Symbol", "NIFTY")
with oc2:
    if st.button("Load chain"):
        try:
            data = get_option_chain(sym.strip().upper(), is_index_symbol(sym))
            if not data["strikes"]:
                st.warning("No strikes found (or blocked). Try again or another symbol.")
            else:
                st.caption(f"Symbol: {data['symbol']} | Expiry: {data['expiry']}")
                # show first 20 rows
                show = data["strikes"][:20]
                st.table(show)
        except Exception as e:
            st.error(f"Option chain error: {e}")

# Gentle auto-refresh using an invisible progress bar
prog = st.empty()
for i in range(0, 100, int(100 * (SIM_TICK_MS / max(100, st_autorefresh_count)))):
    time.sleep(st_autorefresh_count / 1000.0)
    break
# Rerun to advance price
st.experimental_rerun()
