# app.py — Single-file Streamlit Paper Trading App with IST Market-Hours Guard + IST Timestamps

import os
import time
import uuid
import random
import sqlite3
import threading
from datetime import datetime, time as dtime, timedelta
import requests
import streamlit as st

# ----------------------------
# Config
# ----------------------------
STARTING_BALANCE = float(os.getenv("STARTING_BALANCE", "100000"))
SIM_START_PRICE = float(os.getenv("SIM_START_PRICE", "997.28"))
SIM_TICK_MS = int(os.getenv("SIM_TICK_MS", "1500"))
DB_PATH = os.getenv("DB_PATH", "paper_trading.db")

st.set_page_config(page_title="Paper Trading App", layout="wide")

# ----------------------------
# DB connection + init
# ----------------------------
db_lock = threading.Lock()

@st.cache_resource
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            balance REAL NOT NULL,
            created_at INTEGER NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
            qty INTEGER NOT NULL CHECK (qty > 0),
            price REAL NOT NULL,
            ts INTEGER NOT NULL
        )""")
    return conn

conn = get_db()

# ----------------------------
# Helpers
# ----------------------------
def ensure_user(uid, starting_balance=STARTING_BALANCE):
    with db_lock:
        r = conn.execute("SELECT user_id FROM users WHERE user_id=?", (uid,)).fetchone()
        if not r:
            conn.execute("INSERT INTO users VALUES (?,?,?)", (uid, float(starting_balance), int(time.time())))
            conn.commit()

def get_balance(uid):
    r = conn.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
    return float(r["balance"]) if r else 0.0

def get_net_qty(uid):
    r = conn.execute("""SELECT COALESCE(SUM(CASE WHEN side='BUY' THEN qty ELSE -qty END),0) AS net_qty
                        FROM trades WHERE user_id=?""", (uid,)).fetchone()
    return int(r["net_qty"] if r and r["net_qty"] is not None else 0)

def insert_trade(uid, side, qty, price):
    with db_lock:
        conn.execute("INSERT INTO trades (user_id, side, qty, price, ts) VALUES (?,?,?,?,?)",
                     (uid, side, qty, price, int(time.time())))
        conn.commit()

def update_balance(uid, new_balance):
    with db_lock:
        conn.execute("UPDATE users SET balance=? WHERE user_id=?", (float(new_balance), uid))
        conn.commit()

def reset_account(uid, starting_balance=STARTING_BALANCE):
    with db_lock:
        conn.execute("DELETE FROM trades WHERE user_id=?", (uid,))
        conn.execute("UPDATE users SET balance=? WHERE user_id=?", (float(starting_balance), uid))
        conn.commit()

def fetch_history(uid, limit=200):
    rows = conn.execute("SELECT id, side, qty, price, ts FROM trades WHERE user_id=? ORDER BY id DESC LIMIT ?",
                        (uid, limit)).fetchall()
    return [dict(r) for r in rows]

# ----------------------------
# Market-hour guard (UTC→IST)
# ----------------------------
def is_market_open():
    now_utc = datetime.utcnow()
    now_ist = now_utc + timedelta(hours=5, minutes=30)
    return dtime(9, 0) <= now_ist.time() <= dtime(15, 30)

# ----------------------------
# Simulated price
# ----------------------------
def step_price():
    now_ms = int(time.time() * 1000)
    last_price = st.session_state.get("last_price", SIM_START_PRICE)
    last_ts = st.session_state.get("last_ts", now_ms)
    if now_ms - last_ts >= SIM_TICK_MS:
        drift = -0.02 * (last_price - SIM_START_PRICE) / SIM_START_PRICE
        shock = random.uniform(-0.20, 0.20)
        new_p = max(1.0, round(last_price * (1 + drift*0.01 + shock*0.001), 2))
        st.session_state.last_price = new_p
        st.session_state.last_ts = now_ms
    else:
        st.session_state.last_price = last_price
        st.session_state.last_ts = last_ts
    if "price_series" not in st.session_state:
        st.session_state.price_series = []
    st.session_state.price_series.append((now_ms, st.session_state.last_price))
    if len(st.session_state.price_series) > 120:
        st.session_state.price_series = st.session_state.price_series[-120:]
    return st.session_state.last_price

def inr(x):
    try:
        return f"₹ {float(x):,.2f}"
    except:
        return f"₹ {x}"

# ----------------------------
# UI
# ----------------------------
st.title("📈 Paper Trading App (IST Market-Hours Guard)")

if "uid" not in st.session_state:
    st.session_state.uid = str(uuid.uuid4())

with st.sidebar:
    st.markdown("### User & settings")
    uid_input = st.text_input("User ID", st.session_state.uid)
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Load/Create user"):
            st.session_state.uid = uid_input.strip() or st.session_state.uid
            ensure_user(st.session_state.uid, STARTING_BALANCE)
            st.success(f"Active user: {st.session_state.uid}")
    with col_b:
        if st.button("Reset account"):
            ensure_user(st.session_state.uid, STARTING_BALANCE)
            reset_account(st.session_state.uid, STARTING_BALANCE)
            st.session_state.last_price = SIM_START_PRICE
            st.session_state.price_series = []
            st.toast("Account reset.")

ensure_user(st.session_state.uid, STARTING_BALANCE)

last_price = step_price()
qty, bal = get_net_qty(st.session_state.uid), get_balance(st.session_state.uid)
pv = bal + qty * last_price

m1, m2, m3, m4 = st.columns(4)
m1.metric("User ID", st.session_state.uid[-12:])
m2.metric("Balance", inr(bal))
m3.metric("Shares held", f"{qty}")
m4.metric("Portfolio value", inr(pv))

st.subheader("Price chart")
if st.session_state.price_series:
    st.line_chart({"Price": [p for _, p in st.session_state.price_series]}, height=180)
else:
    st.info("Starting price stream…")

# ----------------------------
# Trading panel with guard
# ----------------------------
st.subheader("Trade")
col1, col2 = st.columns([1, 1])
with col1:
    qty_in = st.number_input("Qty", min_value=1, value=1, step=1)
with col2:
    if st.button("BUY"):
        if is_market_open():
            insert_trade(st.session_state.uid, "BUY", qty_in, last_price)
            update_balance(st.session_state.uid, bal - qty_in * last_price)
            st.success(f"BUY {qty_in} @ {last_price}")
        else:
            st.warning("⏰ Market closed. Trades allowed only 9:00 AM – 3:30 PM IST.")
    if st.button("SELL"):
        if is_market_open():
            insert_trade(st.session_state.uid, "SELL", qty_in, last_price)
            update_balance(st.session_state.uid, bal + qty_in * last_price)
            st.success(f"SELL {qty_in} @ {last_price}")
        else:
            st.warning("⏰ Market closed. Trades allowed only 9:00 AM – 3:30 PM IST.")

# ----------------------------
# Trade log (IST display + current time)
# ----------------------------
st.subheader("Trade History")

now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
st.caption(f"🕒 Current IST: {now_ist:%Y-%m-%d %H:%M:%S} • Market open: {is_market_open()}")

def ts_to_ist_str(ts_seconds: int) -> str:
    return (datetime.utcfromtimestamp(ts_seconds) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

history = fetch_history(st.session_state.uid)
if history:
    display_rows = []
    for r in history:
        display_rows.append({
            "id": r["id"],
            "side": r["side"],
            "qty": r["qty"],
            "price": r["price"],
            "ts (IST)": ts_to_ist_str(r["ts"])
        })
    st.dataframe(display_rows, use_container_width=True)
else:
    st.info("No trades yet.")
