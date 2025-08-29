# app.py
# -*- coding: utf-8 -*-
import sqlite3
from dataclasses import dataclass
from typing import Dict, Any, List, Tuple

import streamlit as st
import pandas as pd
import requests
import altair as alt
from datetime import datetime, time as dtime, timedelta
import pytz

# ------------------------------ Page config ------------------------------
st.set_page_config(page_title="Trading OI Dashboard • Single File", page_icon="📊", layout="wide")

# ------------------------------ Settings ------------------------------
IST = pytz.timezone("Asia/Kolkata")
MARKET_OPEN = dtime(9, 0)
MARKET_CLOSE = dtime(15, 30)
INDEX_SYMBOL = "BANKNIFTY"
NSE_OC_URL = f"https://www.nseindia.com/api/option-chain-indices?symbol={INDEX_SYMBOL}"
DB_PATH = "trading_oi.db"

# ------------------------------ Time utils ------------------------------
def now_ist() -> datetime:
    return datetime.now(IST)

def trading_day_str(dt: datetime | None = None) -> str:
    dt = dt or now_ist()
    return dt.strftime("%Y-%m-%d")

def within_market_hours() -> bool:
    t = now_ist().time()
    return MARKET_OPEN <= t <= MARKET_CLOSE

def fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")

# ------------------------------ DB init ------------------------------
def init_db():
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    cur = con.cursor()
    # Trades
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
    # Intraday OI snapshots
    cur.execute("""
        CREATE TABLE IF NOT EXISTS oi_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_ist TEXT NOT NULL,
            trading_day TEXT NOT NULL,
            ce_oi REAL NOT NULL,
            pe_oi REAL NOT NULL,
            fut_price REAL
        )
    """)
    con.commit()
    return con

CON = init_db()

# ------------------------------ NSE fetch ------------------------------
def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/",
        "Connection": "keep-alive",
    })
    try:
        s.get("https://www.nseindia.com", timeout=8)
    except Exception:
        pass
    return s

@st.cache_data(ttl=60, show_spinner=False)
def fetch_option_chain() -> Dict[str, Any]:
    s = new_session()
    r = s.get(NSE_OC_URL, timeout=10)
    r.raise_for_status()
    return r.json()

def parse_chain(json_obj: Dict[str, Any]) -> Tuple[pd.DataFrame, pd.DataFrame, float | None]:
    ce_rows, pe_rows = [], []
    records = json_obj.get("records", {})
    data = records.get("data", [])
    underlying = records.get("underlyingValue")
    for rec in data:
        strike = rec.get("strikePrice")
        ce = rec.get("CE")
        pe = rec.get("PE")
        if strike is None:
            continue
        if ce:
            ce_rows.append((strike, ce.get("openInterest", 0), ce.get("lastPrice", None)))
        if pe:
            pe_rows.append((strike, pe.get("openInterest", 0), pe.get("lastPrice", None)))
    ce_df = pd.DataFrame(ce_rows, columns=["Strike", "CE_OI", "CE_LTP"]).sort_values("Strike")
    pe_df = pd.DataFrame(pe_rows, columns=["Strike", "PE_OI", "PE_LTP"]).sort_values("Strike")
    return ce_df, pe_df, underlying

# ------------------------------ Snapshot logic ------------------------------
def record_oi_snapshot(ce_sum: float, pe_sum: float, fut_price: float | None):
    cur = CON.cursor()
    cur.execute(
        "INSERT INTO oi_snapshots (ts_ist, trading_day, ce_oi, pe_oi, fut_price) VALUES (?,?,?,?,?)",
        (fmt_ts(now_ist()), trading_day_str(), float(ce_sum), float(pe_sum), float(fut_price) if fut_price else None)
    )
    CON.commit()

def load_today_snapshots() -> pd.DataFrame:
    q = "SELECT * FROM oi_snapshots WHERE trading_day = ? ORDER BY id ASC"
    df = pd.read_sql_query(q, CON, params=(trading_day_str(),))
    if not df.empty:
        df["ts"] = pd.to_datetime(df["ts_ist"])
    return df

def maybe_snapshot(ce_sum: float, pe_sum: float, fut_price: float | None, min_gap_sec: int = 60):
    # Append a snapshot only within market hours and if last snapshot is older than min_gap_sec
    if not within_market_hours():
        return
    df = load_today_snapshots()
    if df.empty:
        record_oi_snapshot(ce_sum, pe_sum, fut_price)
        return
    last_ts = pd.to_datetime(df["ts_ist"].iloc[-1]).tz_localize(IST, ambiguous="NaT", nonexistent="NaT")
    # Handle tz if already aware
    if last_ts.tzinfo is None:
        last_ts = last_ts.tz_localize(IST)
    if (now_ist() - last_ts).total_seconds() >= min_gap_sec:
        record_oi_snapshot(ce_sum, pe_sum, fut_price)

# ------------------------------ CE/PE by strike chart ------------------------------
def build_oi_by_strike_chart(merged: pd.DataFrame):
    tidy = merged.melt("Strike", var_name="Type", value_name="OpenInterest")
    chart = (
        alt.Chart(tidy)
        .mark_line(point=True)
        .encode(
            x=alt.X("Strike:Q", title="Strike"),
            y=alt.Y("OpenInterest:Q", title="Open Interest"),
            color=alt.Color("Type:N", title="Series"),
            tooltip=["Strike:Q", "Type:N", "OpenInterest:Q"]
        )
        .properties(height=380)
        .interactive()
    )
    return chart

# ------------------------------ Intraday charts (TradingTick-style) ------------------------------
def build_change_oi_line(df: pd.DataFrame):
    base = alt.Chart(df).encode(x=alt.X("ts:T", title="Time (IST)"))
    # Left axis: CE/PE OI change (fold to single y)
    folded = df.melt(id_vars=["ts","fut_price"], value_vars=["ce_change","pe_change"], var_name="Series", value_name="Change")
    oi_lines = (
        alt.Chart(folded)
        .mark_line()
        .encode(
            x="ts:T",
            y=alt.Y("Change:Q", axis=alt.Axis(title="Change in OI")),
            color=alt.Color("Series:N", scale=alt.Scale(range=["#1f77b4", "#d62728"]), title=""),
            tooltip=["ts:T", "Series:N", alt.Tooltip("Change:Q", format=",.0f")]
        )
    )
    # Right axis: Future price dashed
    fut_line = (
        base.mark_line(strokeDash=[5,3], color="black")
        .encode(y=alt.Y("fut_price:Q", axis=alt.Axis(title="Future price")))
    )
    return alt.layer(oi_lines, fut_line).resolve_scale(y="independent").properties(height=360)

def build_total_oi_line(df: pd.DataFrame):
    base = alt.Chart(df).encode(x=alt.X("ts:T", title="Time (IST)"))
    folded = df.melt(id_vars=["ts","fut_price"], value_vars=["ce_oi","pe_oi"], var_name="Series", value_name="OI")
    oi_lines = (
        alt.Chart(folded)
        .mark_line()
        .encode(
            x="ts:T",
            y=alt.Y("OI:Q", axis=alt.Axis(title="Total OI")),
            color=alt.Color("Series:N", scale=alt.Scale(range=["#17becf", "#d62728"]), title=""),
            tooltip=["ts:T", "Series:N", alt.Tooltip("OI:Q", format=",.0f")]
        )
    )
    fut_line = (
        base.mark_line(strokeDash=[5,3], color="black")
        .encode(y=alt.Y("fut_price:Q", axis=alt.Axis(title="Future price")))
    )
    return alt.layer(oi_lines, fut_line).resolve_scale(y="independent").properties(height=360)

# ------------------------------ Crossover detection ------------------------------
@dataclass
class Crossover:
    strike_below: int
    strike_above: int
    note: str

def find_ce_pe_crossover(merged: pd.DataFrame) -> List[Crossover]:
    out: List[Crossover] = []
    df = merged.sort_values("Strike").dropna(subset=["CE_OI","PE_OI"]).copy()
    df["diff"] = df["CE_OI"] - df["PE_OI"]
    for i in range(1, len(df)):
        prev, cur = df.iloc[i-1], df.iloc[i]
        if prev["diff"] == 0:
            out.append(Crossover(int(prev["Strike"]), int(prev["Strike"]), "Equal OI at strike"))
        elif prev["diff"] * cur["diff"] < 0:
            out.append(Crossover(int(prev["Strike"]), int(cur["Strike"]), "Sign change between strikes"))
    return out

# ------------------------------ Trades & portfolio ------------------------------
def place_trade(symbol: str, strike: int, opt_type: str, side: str, qty: int, price: float):
    cur = CON.cursor()
    cur.execute(
        "INSERT INTO trades (ts_ist, symbol, strike, opt_type, side, qty, price) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (fmt_ts(now_ist()), symbol, strike, opt_type, side, qty, price),
    )
    CON.commit()

def load_trades() -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC", CON)

def compute_positions(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["symbol","strike","opt_type","net_qty","avg_price","invested"])
    trades["signed_qty"] = trades.apply(lambda r: r["qty"] if r["side"]=="BUY" else -r["qty"], axis=1)
    def agg(g: pd.DataFrame) -> pd.Series:
        buy_qty = g.loc[g["signed_qty"]>0, "signed_qty"].sum()
        avg_price = ((g["price"] * g["signed_qty"].clip(lower=0)).sum() / buy_qty) if buy_qty else 0.0
        return pd.Series({"net_qty": g["signed_qty"].sum(), "avg_price": avg_price})
    grouped = trades.groupby(["symbol","strike","opt_type"], as_index=False).apply(agg).reset_index(drop=True)
    grouped["invested"] = grouped["avg_price"] * grouped["net_qty"].clip(lower=0)
    return grouped

def enrich_with_mtm(positions: pd.DataFrame, ce_df: pd.DataFrame, pe_df: pd.DataFrame) -> pd.DataFrame:
    if positions.empty:
        return positions
    latest = (
        ce_df[["Strike","CE_LTP"]].merge(pe_df[["Strike","PE_LTP"]], on="Strike", how="outer")
    )
    positions = positions.merge(latest, left_on="strike", right_on="Strike", how="left")
    positions["ltp"] = positions.apply(lambda r: r["CE_LTP"] if r["opt_type"]=="CE" else r["PE_LTP"], axis=1)
    positions["ltp"] = positions["ltp"].fillna(0.0)
    positions["mtm"] = (positions["ltp"] - positions["avg_price"]) * positions["net_qty"]
    positions["pnl_pct"] = positions.apply(
        lambda r: ((r["ltp"] - r["avg_price"]) / r["avg_price"] * 100) if r["avg_price"] else 0.0, axis=1
    )
    return positions[["symbol","strike","opt_type","net_qty","avg_price","ltp","mtm","pnl_pct"]].sort_values(["opt_type","strike"])

# ------------------------------ UI ------------------------------
st.title("📊 Intraday OI — Change & Total (TradingTick‑style)")
st.caption(f"Last updated: {fmt_ts(now_ist())}")
if not within_market_hours():
    st.warning("Market is closed (IST 9:00–15:30). Data may be stale; snapshots won't record.")

top_l, top_r = st.columns([1, 1])
with top_l:
    if st.button("🔄 Refresh (clear cache)", use_container_width=True):
        st.cache_data.clear()
with top_r:
    snap_gap = st.slider("Snapshot minimum gap (seconds)", min_value=30, max_value=300, value=60, step=30, help="New row appended no sooner than this gap during market hours.")

# Fetch live option chain
try:
    oc = fetch_option_chain()
    ce_df, pe_df, underlying = parse_chain(oc)
    merged = pd.merge(ce_df[["Strike","CE_OI"]], pe_df[["Strike","PE_OI"]], on="Strike", how="inner").sort_values("Strike")
    ce_sum = float(ce_df["CE_OI"].sum()) if not ce_df.empty else 0.0
    pe_sum = float(pe_df["PE_OI"].sum()) if not pe_df.empty else 0.0
    fut_px = float(underlying) if underlying is not None else None
except Exception as e:
    st.error(f"Option chain fetch failed: {e}")
    # Minimal fallback to keep UI responsive
    strikes = list(range(44000, 45500, 100))
    ce_vals = [1_500_000 - i*50_000 for i in range(len(strikes))]
    pe_vals = [500_000 + i*50_000 for i in range(len(strikes))]
    ce_df = pd.DataFrame({"Strike": strikes, "CE_OI": ce_vals, "CE_LTP": [300 - i*5 for i in range(len(strikes))]})
    pe_df = pd.DataFrame({"Strike": strikes, "PE_OI": pe_vals, "PE_LTP": [120 + i*5 for i in range(len(strikes))]})
    merged = pd.merge(ce_df[["Strike","CE_OI"]], pe_df[["Strike","PE_OI"]], on="Strike")
    ce_sum, pe_sum, fut_px = float(sum(ce_vals)), float(sum(pe_vals)), None

# Append snapshot (rate‑limited)
maybe_snapshot(ce_sum, pe_sum, fut_px, min_gap_sec=int(snap_gap))
snap_df = load_today_snapshots()

# Panels: Change in OI and Total OI (with CALL/PUT bars)
p1, p2 = st.columns([1, 1], gap="large")

with p1:
    st.subheader("Change in OI")
    if snap_df.empty or len(snap_df) < 1:
        st.info("No snapshots yet. Wait for first capture during market hours or click Refresh after a minute.")
    else:
        # Baseline = first row of the day
        base_ce, base_pe = snap_df["ce_oi"].iloc[0], snap_df["pe_oi"].iloc[0]
        df = snap_df.copy()
        df["ts"] = pd.to_datetime(df["ts_ist"])
        df["ce_change"] = df["ce_oi"] - base_ce
        df["pe_change"] = df["pe_oi"] - base_pe
        # Bars: latest change
        last_ce_chg = float(df["ce_change"].iloc[-1])
        last_pe_chg = float(df["pe_change"].iloc[-1])
        bar_df = pd.DataFrame({"Type":["CALL","PUT"], "Change":[last_ce_chg, last_pe_chg]})
        bars = alt.Chart(bar_df).mark_bar().encode(
            x=alt.X("Type:N", title=None),
            y=alt.Y("Change:Q", title="Change in OI"),
            color=alt.Color("Type:N", scale=alt.Scale(range=["#1f77b4", "#d62728"]), legend=None),
            tooltip=[alt.Tooltip("Change:Q", format=",.0f")]
        ).properties(height=160)
        st.altair_chart(bars, use_container_width=True)
        st.altair_chart(build_change_oi_line(df), use_container_width=True)

with p2:
    st.subheader("Total OI")
    if snap_df.empty or len(snap_df) < 1:
        st.info("No snapshots yet. Wait for first capture during market hours or click Refresh after a minute.")
    else:
        df2 = snap_df.copy()
        df2["ts"] = pd.to_datetime(df2["ts_ist"])
        # Bars: latest total
        bar2_df = pd.DataFrame({"Type":["CALL","PUT"], "Total":[float(df2["ce_oi"].iloc[-1]), float(df2["pe_oi"].iloc[-1])]})
        bars2 = alt.Chart(bar2_df).mark_bar().encode(
            x=alt.X("Type:N", title=None),
            y=alt.Y("Total:Q", title="Total OI"),
            color=alt.Color("Type:N", scale=alt.Scale(range=["#17becf", "#d62728"]), legend=None),
            tooltip=[alt.Tooltip("Total:Q", format=",.0f")]
        ).properties(height=160)
        st.altair_chart(bars2, use_container_width=True)
        st.altair_chart(build_total_oi_line(df2), use_container_width=True)

st.divider()

# CE vs PE by strike panel (live)
c1, c2 = st.columns([2, 1])
with c1:
    st.subheader("CE vs PE OI by strike")
    st.altair_chart(build_oi_by_strike_chart(merged), use_container_width=True)
    st.dataframe(merged.rename(columns={"CE_OI":"CE OI","PE_OI":"PE OI"}), use_container_width=True, height=300)

    crosses = find_ce_pe_crossover(merged)
    if crosses:
        st.markdown("**Crossovers detected:**")
        st.write("\n".join([f"- Between {c.strike_below} and {c.strike_above} ({c.note})" for c in crosses[:6]]))
    else:
        st.info("No CE/PE OI crossover detected in visible strikes.")

with c2:
    st.subheader("Trade panel")
    strikes = merged["Strike"].tolist()
    if not strikes:
        st.info("No strikes available.")
    else:
        opt_type = st.selectbox("Option type", ["CE","PE"], index=0)
        strike = st.selectbox("Strike", strikes, index=len(strikes)//2)
        # Use LTP estimate from CE_LTP/PE_LTP columns
        sel_price = None
        if opt_type == "CE":
            row = ce_df[ce_df["Strike"]==strike]
            sel_price = float(row["CE_LTP"].iloc[0]) if not row.empty and pd.notna(row["CE_LTP"].iloc[0]) else 0.0
        else:
            row = pe_df[pe_df["Strike"]==strike]
            sel_price = float(row["PE_LTP"].iloc[0]) if not row.empty and pd.notna(row["PE_LTP"].iloc[0]) else 0.0
        st.metric("Current LTP", f"{sel_price:.2f}")
        qty = st.number_input("Quantity", min_value=1, max_value=2000, step=25, value=25)
        disable_trading = not within_market_hours()
        b1, b2 = st.columns(2)
        with b1:
            if st.button("Buy", type="primary", disabled=disable_trading or sel_price == 0.0, use_container_width=True):
                place_trade(INDEX_SYMBOL, int(strike), opt_type, "BUY", int(qty), sel_price)
                st.success(f"BUY {opt_type} {strike} x {qty} @ {sel_price:.2f}")
        with b2:
            if st.button("Sell", disabled=disable_trading or sel_price == 0.0, use_container_width=True):
                place_trade(INDEX_SYMBOL, int(strike), opt_type, "SELL", int(qty), sel_price)
                st.success(f"SELL {opt_type} {strike} x {qty} @ {sel_price:.2f}")

st.subheader("Portfolio")
trades_df = load_trades()
positions = enrich_with_mtm(compute_positions(trades_df), ce_df, pe_df)
if positions.empty:
    st.info("No open positions.")
else:
    st.dataframe(positions, use_container_width=True)
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

st.caption("Single-file. Data cached 60s. Snapshots append during market hours only. Source: NSE option chain for indices.")
