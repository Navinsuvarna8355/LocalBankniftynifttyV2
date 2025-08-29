# app.py
# Requirements (Streamlit Cloud): streamlit==1.36.0, pandas, requests, altair
# This is a single-file, production-ready multi-index OI dashboard.

import streamlit as st
import pandas as pd
import altair as alt
import requests
import time
from datetime import datetime, timedelta

# --------------- CONFIG ---------------
st.set_page_config(page_title="Multi-Index OI Dashboard", layout="wide")
alt.themes.enable("opaque")

INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

# Mapping to NSE "allIndices" names
INDEX_NAME_MAP = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MIDCAP SELECT",
}

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                  " AppleWebKit/537.36 (KHTML, like Gecko)"
                  " Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# --------------- HELPERS ---------------
def ist_now():
    # IST = UTC + 5:30
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

def market_open_ist(now=None):
    # NSE equity hours: 09:15–15:30 IST, Mon–Fri
    n = now or ist_now()
    if n.weekday() >= 5:
        return False
    start = n.replace(hour=9, minute=15, second=0, microsecond=0)
    end = n.replace(hour=15, minute=30, second=0, microsecond=0)
    return start <= n <= end

def get_session():
    s = requests.Session()
    s.headers.update(NSE_HEADERS)
    # Warm up cookies
    try:
        s.get("https://www.nseindia.com/", timeout=8)
    except Exception:
        pass
    return s

def _retry_get_json(url, params=None, retries=3, backoff=0.75):
    last_err = None
    for attempt in range(retries):
        try:
            s = get_session()
            r = s.get(url, params=params, timeout=8)
            if r.status_code == 200:
                return r.json()
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = repr(e)
        time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")

@st.cache_data(ttl=30, show_spinner=False)
def fetch_all_indices():
    # Snapshot for tiles (LTP, change, %)
    url = "https://www.nseindia.com/api/allIndices"
    data = _retry_get_json(url)
    if not isinstance(data, dict) or "data" not in data:
        return {}
    by_name = {}
    for row in data.get("data", []):
        name = row.get("index")
        if not name:
            continue
        ltp = row.get("last", row.get("lastPrice", None))
        change = row.get("variation", row.get("change", None))
        pchange = row.get("percentChange", row.get("pChange", None))
        by_name[name] = {
            "ltp": ltp,
            "change": change,
            "pchange": pchange,
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "prevClose": row.get("previousClose", row.get("prevClose")),
            "time": row.get("timeVal"),
        }
    return by_name

@st.cache_data(ttl=45, show_spinner=False)
def fetch_option_chain(symbol: str):
    url = f"https://www.nseindia.com/api/option-chain-indices"
    data = _retry_get_json(url, params={"symbol": symbol})
    # Parse
    rec = data.get("records", {})
    under = rec.get("underlyingValue", None)
    rows = rec.get("data", [])
    parsed = []
    for r in rows:
        strike = r.get("strikePrice")
        ce = r.get("CE")
        pe = r.get("PE")
        parsed.append({
            "strike": strike,
            "ce_oi": ce.get("openInterest") if ce else 0,
            "pe_oi": pe.get("openInterest") if pe else 0,
            "ce_chg_oi": ce.get("changeinOpenInterest") if ce else 0,
            "pe_chg_oi": pe.get("changeinOpenInterest") if pe else 0,
            "ce_ltp": ce.get("lastPrice") if ce else None,
            "pe_ltp": pe.get("lastPrice") if pe else None,
        })
    df = pd.DataFrame(parsed).dropna(subset=["strike"])
    df = df.sort_values("strike").reset_index(drop=True)
    return under, df

def compute_pcr(df: pd.DataFrame):
    ce_sum = float(df["ce_oi"].sum() or 0)
    pe_sum = float(df["pe_oi"].sum() or 0)
    return (pe_sum / ce_sum) if ce_sum > 0 else None

def compute_max_pain(df: pd.DataFrame):
    # Approx max pain: sum OI * payoff distance at each candidate strike
    strikes = df["strike"].astype(float).values
    ce_oi = df["ce_oi"].astype(float).values
    pe_oi = df["pe_oi"].astype(float).values
    pains = []
    for s in strikes:
        call_pain = ((strikes - s).clip(min=0) * ce_oi).sum()
        put_pain = ((s - strikes).clip(min=0) * pe_oi).sum()
        pains.append(call_pain + put_pain)
    if len(pains) == 0:
        return None, None
    idx_min = int(pd.Series(pains).idxmin())
    return strikes[idx_min], pains[idx_min]

def sr_levels(df: pd.DataFrame, k: int = 3):
    # Supports: top-k PE OI strikes, Resistances: top-k CE OI strikes
    top_pe = df.nlargest(k, "pe_oi")[["strike", "pe_oi"]]
    top_ce = df.nlargest(k, "ce_oi")[["strike", "ce_oi"]]
    return list(top_pe["strike"].astype(int)), list(top_ce["strike"].astype(int))

def upsert_history(idx_key: str, ltp: float, max_points: int = 200):
    if "hist" not in st.session_state:
        st.session_state["hist"] = {}
    hist = st.session_state["hist"].setdefault(idx_key, [])
    now = ist_now()
    # Append only if new or changed
    if not hist or hist[-1][1] != ltp:
        hist.append((now, float(ltp)))
    # Trim
    if len(hist) > max_points:
        st.session_state["hist"][idx_key] = hist[-max_points:]

def get_history_frame(indices):
    # Build a tidy dataframe for sidebar compare
    rows = []
    for idx in indices:
        for t, v in st.session_state.get("hist", {}).get(idx, []):
            rows.append({"index": idx, "time": t, "ltp": v})
    if not rows:
        return pd.DataFrame(columns=["index", "time", "ltp"])
    return pd.DataFrame(rows)

def tag_new_levels(symbol: str, supports, resistances):
    key = f"levels_{symbol}"
    prev = st.session_state.get(key, {"supports": [], "resistances": []})
    new_supports = [s for s in supports if s not in prev["supports"]]
    new_resistances = [r for r in resistances if r not in prev["resistances"]]
    st.session_state[key] = {"supports": supports, "resistances": resistances}
    return new_supports, new_resistances

def tiny_sparkline(df: pd.DataFrame):
    if df.empty:
        return alt.Chart(pd.DataFrame({"x": [], "y": []})).mark_line()
    base = alt.Chart(df).encode(x=alt.X("time:T", axis=None), y=alt.Y("ltp:Q", axis=None))
    line = base.mark_line(color="#6AA84F", strokeWidth=2)
    area = base.mark_area(color="#6AA84F", opacity=0.15)
    return (area + line).properties(height=40)

def crossover_points(df: pd.DataFrame, threshold_ratio=0.1):
    # Mark strikes where CE and PE OI are close (potential crossovers)
    # threshold = threshold_ratio * max(CE_OI, PE_OI) at that strike
    if df.empty:
        return pd.DataFrame(columns=["strike", "label"])
    rows = []
    for _, r in df.iterrows():
        ce = float(r["ce_oi"] or 0)
        pe = float(r["pe_oi"] or 0)
        mx = max(ce, pe)
        if mx == 0:
            continue
        if abs(ce - pe) <= threshold_ratio * mx:
            rows.append({"strike": r["strike"], "label": "near crossover"})
    return pd.DataFrame(rows)

# --------------- UI: HEADER + GUARDS ---------------
st.markdown("## Multi-index OI scanner")
col_head_left, col_head_right = st.columns([0.7, 0.3])
with col_head_left:
    st.caption("Quickly scan NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY — switch instantly, watch OI structure and levels update in real time.")
with col_head_right:
    auto = st.toggle("Auto-refresh every 30s", value=True)
    if auto:
        st.experimental_set_query_params(ts=str(int(time.time())))  # bust cache on URL
        st.autorefresh = st.empty()
        st.autorefresh.write("")  # placeholder

if not market_open_ist():
    st.info("Market appears closed (IST). Data may be static or limited. You can still explore historical OI structure.")

# --------------- SNAPSHOT TILES ---------------
snap = {}
try:
    all_idx = fetch_all_indices()
except Exception as e:
    st.warning(f"Index snapshot unavailable right now. Falling back to option-chain LTPs. ({e})")
    all_idx = {}

tile_cols = st.columns(len(INDICES))
clicked = None

for i, sym in enumerate(INDICES):
    idx_name = INDEX_NAME_MAP.get(sym, sym)
    dat = all_idx.get(idx_name, {})
    ltp = dat.get("ltp")
    change = dat.get("change")
    pchg = dat.get("pchange")

    # Fallback to OC underlying if snapshot missing
    if ltp is None:
        try:
            under, _ = fetch_option_chain(sym)
            ltp = under
        except Exception:
            ltp = None

    # Update history
    if ltp is not None:
        upsert_history(sym, float(ltp))

    with tile_cols[i]:
        with st.container(border=True):
            c1, c2 = st.columns([0.55, 0.45])
            with c1:
                st.markdown(f"**{sym}**")
                st.markdown(f"{(ltp if ltp is not None else '—')}")
                if change is not None and pchg is not None:
                    color = "🟢" if float(change) >= 0 else "🔻"
                    st.caption(f"{color} {change:+.2f} ({pchg:+.2f}%)")
                else:
                    st.caption("—")
            with c2:
                hist_df = get_history_frame([sym])
                spark = tiny_sparkline(hist_df[hist_df["index"] == sym])
                st.altair_chart(spark, use_container_width=True)

            if st.button("Open", key=f"btn_{sym}", use_container_width=True):
                clicked = sym

# --------------- SELECTED SYMBOL ---------------
selected = clicked or st.session_state.get("symbol_choice", "BANKNIFTY")
st.session_state["symbol_choice"] = selected

st.markdown("---")
st.subheader(f"Detailed view: {selected}")

# --------------- FETCH OC DATA ---------------
try:
    underlying, oc_df = fetch_option_chain(selected)
except Exception as e:
    st.error(f"Option chain unavailable for {selected} right now. Try again shortly. ({e})")
    st.stop()

if underlying is not None:
    st.caption(f"Underlying: {underlying:.2f}")

# --------------- TABS ---------------
tab1, tab2, tab3 = st.tabs(["CE vs PE OI", "Change in OI", "Max Pain • PCR • Levels"])

# Tab 1: CE vs PE OI + crossover markers + underlying vline
with tab1:
    if oc_df.empty:
        st.write("No option data.")
    else:
        long_df = oc_df.melt(
            id_vars=["strike"],
            value_vars=["ce_oi", "pe_oi"],
            var_name="type",
            value_name="oi",
        )
        long_df["type"] = long_df["type"].map({"ce_oi": "CE OI", "pe_oi": "PE OI"})

        base = alt.Chart(long_df).encode(
            x=alt.X("strike:Q", title="Strike"),
            y=alt.Y("oi:Q", title="Open Interest"),
            color=alt.Color("type:N", scale=alt.Scale(range=["#FF8C42", "#3B82F6"])),
            tooltip=[
                alt.Tooltip("strike:Q", title="Strike"),
                alt.Tooltip("type:N", title="Type"),
                alt.Tooltip("oi:Q", title="OI", format=","),
            ],
        )

        bars = base.mark_bar(size=8)
        chart = bars

        # Underlying vertical line
        if underlying:
            vline = alt.Chart(pd.DataFrame({"x": [underlying]})).mark_rule(
                color="#22C55E", strokeWidth=2
            ).encode(x="x:Q")
            chart = chart + vline

        # Crossover markers
        cross_df = crossover_points(oc_df)
        if not cross_df.empty:
            cross_mark = alt.Chart(cross_df).mark_point(
                shape="triangle-up", color="#6366F1", size=80
            ).encode(x="strike:Q", y=alt.value(0))
            chart = chart + cross_mark

        st.altair_chart(chart.properties(height=380), use_container_width=True)

# Tab 2: Change in OI intraday
with tab2:
    if oc_df.empty:
        st.write("No option data.")
    else:
        delta_df = oc_df.melt(
            id_vars=["strike"],
            value_vars=["ce_chg_oi", "pe_chg_oi"],
            var_name="type",
            value_name="chg_oi",
        )
        delta_df["type"] = delta_df["type"].map({"ce_chg_oi": "CE ΔOI", "pe_chg_oi": "PE ΔOI"})

        base = alt.Chart(delta_df).encode(
            x=alt.X("strike:Q", title="Strike"),
            y=alt.Y("chg_oi:Q", title="Change in OI"),
            color=alt.Color("type:N", scale=alt.Scale(range=["#F59E0B", "#60A5FA"])),
            tooltip=[
                alt.Tooltip("strike:Q", title="Strike"),
                alt.Tooltip("type:N", title="Type"),
                alt.Tooltip("chg_oi:Q", title="Δ OI", format=","),
            ],
        )
        bars = base.mark_bar(size=8)
        hzero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#9CA3AF").encode(y="y:Q")
        st.altair_chart((bars + hzero).properties(height=300), use_container_width=True)

# Tab 3: Max Pain, PCR, Support/Resistance with NEW tags
with tab3:
    c1, c2, c3 = st.columns([0.3, 0.35, 0.35])

    with c1:
        pcr = compute_pcr(oc_df)
        mp_strike, _ = compute_max_pain(oc_df)
        st.markdown("**Summary:**")
        st.metric("PCR (Total OI)", f"{pcr:.2f}" if pcr else "—")
        st.metric("Max Pain", f"{int(mp_strike)}" if mp_strike else "—")
        if underlying and mp_strike:
            diff = underlying - mp_strike
            st.caption(f"Distance to Max Pain: {diff:+.0f}")

    with c2:
        sup, res = sr_levels(oc_df, k=3)
        new_sup, new_res = tag_new_levels(selected, sup, res)
        st.markdown("**Support (Top PE OI):**")
        for s in sup:
            tag = " 🆕" if s in new_sup else ""
            st.write(f"- {s}{tag}")
    with c3:
        st.markdown("**Resistance (Top CE OI):**")
        for r in res:
            tag = " 🆕" if r in new_res else ""
            st.write(f"- {r}{tag}")

# --------------- SIDEBAR: QUICK COMPARE ---------------
st.sidebar.header("Quick compare")
hist_all = get_history_frame(INDICES)
if hist_all.empty:
    st.sidebar.caption("Waiting for snapshot to build.")
else:
    # Normalize times so Altair treats as continuous
    chart = alt.Chart(hist_all).mark_line().encode(
        x=alt.X("time:T", title=None),
        y=alt.Y("ltp:Q", title=None),
        color=alt.Color("index:N", legend=alt.Legend(orient="bottom", title=None)),
        tooltip=[alt.Tooltip("index:N"), alt.Tooltip("time:T"), alt.Tooltip("ltp:Q")],
    ).properties(height=180)
    st.sidebar.altair_chart(chart, use_container_width=True)

# --------------- FOOTER + REFRESH ---------------
with st.sidebar.expander("Settings"):
    st.caption("History length per index")
    max_points = st.slider("Max points", 50, 500, 200, 10)
    # Enforce max_points (trim)
    if "hist" in st.session_state:
        for k in list(st.session_state["hist"].keys()):
            h = st.session_state["hist"][k]
            if len(h) > max_points:
                st.session_state["hist"][k] = h[-max_points:]

st.caption("Data: NSE public endpoints. If data stalls, it usually recovers on the next refresh window.")

# Auto-refresh every 30s (soft)
if auto:
    st.experimental_rerun()
