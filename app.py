# app.py
# -*- coding: utf-8 -*-
import os
import sqlite3
from dataclasses import dataclass
from typing import Tuple, Dict, Any, List

import pandas as pd
import requests
import altair as alt
import streamlit as st
from datetime import datetime, time as dtime
import pytz

# ------------------------------ Page config ------------------------------
st.set_page_config(page_title="CE vs PE OI • Paper Trading", page_icon="📊", layout="wide")

# ------------------------------ Settings ------------------------------
IST = pytz.timezone("Asia/Kolkata")
MARKET_OPEN = dtime(9, 0)
MARKET_CLOSE = dtime(15, 30)
INDEX_SYMBOL = "BANKNIFTY"
NSE_OC_URL = f"https://www.nseindia.com/api/option-chain-indices?symbol={INDEX_SYMBOL}"
DB_PATH = "trades.db"

# ------------------------------ Utilities ------------------------------
def now_ist() -> datetime:
    return datetime.now(IST)

def within_market_hours() -> bool:
    t = now_ist().time()
    return MARKET_OPEN <= t <= MARKET_CLOSE

def fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")

# ------------------------------ DB Init ------------------------------
def init_db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_ist TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strike INTEGER NOT NULL,
            opt_type TEXT CHECK(opt_type IN ('CE','PE')) NOT NULL,
            side TEXT CHECK(side IN ('BUY','SELL')) NOT NULL,
            qty INTEGER NOT NULL,
            price REAL NOT NULL
        )
    """)
    con.commit()
    return con

CON = init_db()

# ------------------------------ NSE Session ------------------------------
def build_nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/",
        "Connection": "keep-alive",
    })
    # Prime cookies
    try:
        s.get("https://www.nseindia.com", timeout=8)
    except Exception:
        pass
    return s

SESSION = build_nse_session()

# ------------------------------ Data fetch & cache ------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def fetch_option_chain() -> Dict[str, Any]:
    r = SESSION.get(NSE_OC_URL, timeout=10)
    r.raise_for_status()
    return r.json()

def parse_chain(json_obj: Dict[str, Any]) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    # Build CE/PE OI and prices by strike
    ce_rows, pe_rows, px_rows = [], [], []
    records = json_obj.get("records", {}).get("data", [])
    for rec in records:
        strike = rec.get("strikePrice")
        ce = rec.get("CE")
        pe = rec.get("PE")
        if strike is None:
            continue
        if ce:
            ce_rows.append((strike, ce.get("openInterest", 0), ce.get("lastPrice", None)))
            if ce.get("lastPrice") is not None:
                px_rows.append((strike, "CE", ce.get("lastPrice")))
        if pe:
            pe_rows.append((strike, pe.get("openInterest", 0), pe.get("lastPrice", None)))
            if pe.get("lastPrice") is not None:
                px_rows.append((strike, "PE", pe.get("lastPrice")))
    ce_df = pd.DataFrame(ce_rows, columns=["Strike", "CE_OI", "CE_LTP"]).sort_values("Strike")
    pe_df = pd.DataFrame(pe_rows, columns=["Strike", "PE_OI", "PE_LTP"]).sort_values("Strike")
    px_df = pd.DataFrame(px_rows, columns=["Strike", "Type", "LTP"]).sort_values(["Strike", "Type"])
    return ce_df, pe_df, px_df

def build_chart(merged: pd.DataFrame):
    m = merged.melt("Strike", var_name="Type", value_name="OpenInterest")
    chart = (
        alt.Chart(m)
        .mark_line(point=True)
        .encode(
            x=alt.X("Strike:Q", title="Strike"),
            y=alt.Y("OpenInterest:Q", title="Open Interest"),
            color=alt.Color("Type:N", title="Series"),
            tooltip=["Strike:Q", "Type:N", "OpenInterest:Q"]
        )
        .properties(height=420)
        .interactive()
    )
    return chart

# ------------------------------ Crossover detection ------------------------------
@dataclass
class Crossover:
    strike_below: int
    strike_above: int
    note: str

def find_ce_pe_crossover(merged: pd.DataFrame) -> List[Crossover]:
    # Look for sign changes in diff = CE_OI - PE_OI
    out: List[Crossover] = []
    df = merged.sort_values("Strike").dropna(subset=["CE_OI", "PE_OI"]).copy()
    df["diff"] = df["CE_OI"] - df["PE_OI"]
    for i in range(1, len(df)):
        prev, cur = df.iloc[i - 1], df.iloc[i]
        if prev["diff"] == 0:
            out.append(Crossover(int(prev["Strike"]), int(prev["Strike"]), "Equal OI at strike"))
        elif prev["diff"] * cur["diff"] < 0:
            out.append(Crossover(int(prev["Strike"]), int(cur["Strike"]), "Sign change between strikes"))
    return out

# ------------------------------ Portfolio & PnL ------------------------------
def place_trade(symbol: str, strike: int, opt_type: str, side: str, qty: int, price: float):
    cur = CON.cursor()
    cur.execute(
        "INSERT INTO trades (ts_ist, symbol, strike, opt_type, side, qty, price) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (fmt_ts(now_ist()), symbol, strike, opt_type, side, qty, price),
    )
    CON.commit()

def load_trades() -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC", CON)
    return df

def compute_positions(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["symbol","strike","opt_type","net_qty","avg_price","invested"])
    # BUY positive qty, SELL negative qty
    trades["signed_qty"] = trades.apply(lambda r: r["qty"] if r["side"] == "BUY" else -r["qty"], axis=1)
    # Weighted average price
    grouped = trades.groupby(["symbol","strike","opt_type"], as_index=False).apply(
        lambda g: pd.Series({
            "net_qty": g["signed_qty"].sum(),
            "avg_price": ( (g["price"] * g["signed_qty"].clip(lower=0)).sum() / max(g["signed_qty"].clip(lower=0).sum(), 1) )
        })
    ).reset_index(drop=True)
    grouped["invested"] = grouped["avg_price"] * grouped["net_qty"].clip(lower=0)
    return grouped

def enrich_with_mark_to_market(positions: pd.DataFrame, px_df: pd.DataFrame) -> pd.DataFrame:
    if positions.empty:
        return positions
    # current price map by (strike,type)
    latest = px_df.pivot(index="Strike", columns="Type", values="LTP").reset_index()
    positions = positions.merge(latest, left_on="strike", right_on="Strike", how="left")
    positions["ltp"] = positions.apply(lambda r: r["CE"] if r["opt_type"] == "CE" else r["PE"], axis=1)
    positions["ltp"] = positions["ltp"].fillna(0.0)
    positions["mtm"] = (positions["ltp"] - positions["avg_price"]) * positions["net_qty"]
    positions["pnl_pct"] = positions.apply(
        lambda r: (r["ltp"] - r["avg_price"]) / r["avg_price"] * 100 if r["avg_price"] else 0.0, axis=1
    )
    show_cols = ["symbol","strike","opt_type","net_qty","avg_price","ltp","mtm","pnl_pct"]
    return positions[show_cols].sort_values(["opt_type","strike"])

# ------------------------------ API mode (optional) ------------------------------
def maybe_api_mode(merged: pd.DataFrame):
    qp = st.query_params
    if qp.get("api", [""])[0].lower() == "option_chain":
        st.write(merged.to_dict(orient="records"))
        st.stop()

# ------------------------------ UI ------------------------------
st.title("📊 CE vs PE OI (Live Option Chain) — Paper Trading")
st.caption(f"Last updated: {fmt_ts(now_ist())}")
if not within_market_hours():
    st.warning("Market is closed (IST 9:00–15:30). You can still view data; trading is disabled.")

# Controls row
left, mid, right = st.columns([1, 1, 1])
with left:
    if st.button("🔄 Refresh data (clear cache)", use_container_width=True):
        st.cache_data.clear()

with mid:
    st.write("")

with right:
    st.toggle("Dark theme hint", value=False, help="Use Streamlit theme switch if available in your settings.")

# Fetch data
try:
    data = fetch_option_chain()
    ce_df, pe_df, px_df = parse_chain(data)
    merged = pd.merge(ce_df[["Strike","CE_OI"]], pe_df[["Strike","PE_OI"]], on="Strike", how="inner").sort_values("Strike")
except Exception as e:
    st.error(f"Option chain fetch failed: {e}")
    # Fallback synthetic data to keep the app usable
    import numpy as np
    strikes = list(range(44000, 45500, 100))
    ce_oi = np.linspace(1_500_000, 500_000, len(strikes)).astype(int)
    pe_oi = np.linspace(500_000, 1_500_000, len(strikes)).astype(int)
    ce_df = pd.DataFrame({"Strike": strikes, "CE_OI": ce_oi, "CE_LTP": np.linspace(300, 120, len(strikes))})
    pe_df = pd.DataFrame({"Strike": strikes, "PE_OI": pe_oi, "PE_LTP": np.linspace(120, 300, len(strikes))})
    px_df = pd.DataFrame({"Strike": strikes * 2, "Type": ["CE","PE"] * len(strikes), "LTP": list(ce_df["CE_LTP"]) + list(pe_df["PE_LTP"])})
    merged = pd.merge(ce_df[["Strike","CE_OI"]], pe_df[["Strike","PE_OI"]], on="Strike", how="inner")

# API mode if requested
maybe_api_mode(merged)

# Layout: Chart on left, details on right
c1, c2 = st.columns([2, 1])

with c1:
    st.subheader("CE vs PE OI by strike")
    st.altair_chart(build_chart(merged), use_container_width=True)
    st.dataframe(merged.rename(columns={"CE_OI":"CE OI","PE_OI":"PE OI"}), use_container_width=True, height=300)

    # Crossover insights
    crosses = find_ce_pe_crossover(merged)
    if crosses:
        lines = [f"- Between {c.strike_below} and {c.strike_above} ({c.note})" for c in crosses[:5]]
        st.markdown("**Crossovers detected:**")
        st.write("\n".join(lines))
    else:
        st.info("No CE/PE OI crossover detected in visible strikes.")

with c2:
    st.subheader("Trade panel")
    # Build selection lists
    strikes = merged["Strike"].tolist()
    opt_type = st.selectbox("Option type", options=["CE","PE"], index=0)
    strike = st.selectbox("Strike", options=strikes, index=len(strikes)//2 if strikes else 0)
    # Current LTP for selected
    sel_price = None
    if not px_df.empty and strike in px_df["Strike"].unique():
        p = px_df[px_df["Strike"] == strike]
        if opt_type == "CE":
            sel_price = p[p["Type"]=="CE"]["LTP"].max()
        else:
            sel_price = p[p["Type"]=="PE"]["LTP"].max()
    sel_price = float(sel_price) if pd.notna(sel_price) else 0.0
    st.metric(label="Current LTP", value=f"{sel_price:.2f}")
    qty = st.number_input("Quantity", min_value=1, max_value=2000, step=25, value=25)

    disable_trading = not within_market_hours()
    c_buy, c_sell = st.columns(2)
    with c_buy:
        if st.button("Buy", type="primary", disabled=disable_trading or sel_price == 0.0, use_container_width=True):
            place_trade(INDEX_SYMBOL, int(strike), opt_type, "BUY", int(qty), sel_price)
            st.success(f"BUY {opt_type} {strike} x {qty} @ {sel_price:.2f}")
    with c_sell:
        if st.button("Sell", disabled=disable_trading or sel_price == 0.0, use_container_width=True):
            place_trade(INDEX_SYMBOL, int(strike), opt_type, "SELL", int(qty), sel_price)
            st.success(f"SELL {opt_type} {strike} x {qty} @ {sel_price:.2f}")

# Portfolio and history
st.subheader("Portfolio")
trades_df = load_trades()
positions = compute_positions(trades_df)
positions = enrich_with_mark_to_market(positions, px_df)
if positions.empty:
    st.info("No open positions.")
else:
    st.dataframe(positions, use_container_width=True)
    # Totals
    total_mtm = float(positions["mtm"].sum())
    total_invested = float((positions["avg_price"] * positions["net_qty"].clip(lower=0)).sum())
    pnl_pct = (total_mtm / total_invested * 100) if total_invested else 0.0
    m1, m2, m3 = st.columns(3)
    m1.metric("Total MTM", f"{total_mtm:,.2f}")
    m2.metric("Invested (approx)", f"{total_invested:,.2f}")
    m3.metric("PnL %", f"{pnl_pct:.2f}%")

st.subheader("Transaction history")
if trades_df.empty:
    st.info("No trades yet.")
else:
    st.dataframe(trades_df, use_container_width=True, height=320)

# Footer
st.caption("Paper trading only. Data cached 60s. Option chain source: NSE.")
