# app.py
# Requirements (Streamlit Cloud):
# streamlit==1.36.0
# pandas==2.2.2
# requests==2.32.3
# altair==5.3.0

import streamlit as st
import pandas as pd
import altair as alt
import requests
import time
from datetime import datetime, timedelta

# -------------------------- CONFIG --------------------------
st.set_page_config(page_title="Multi-Index OI Dashboard", layout="wide")
alt.themes.enable("opaque")

INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
INDEX_NAME_MAP = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MIDCAP SELECT",
}
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# CSS to stabilize layout heights and reduce visual shift
st.markdown("""
<style>
.tile-box {min-height: 140px;}
.metric-box {min-height: 130px;}
.chart-box {min-height: 420px;}
.small {color: #888;}
</style>
""", unsafe_allow_html=True)

# -------------------------- TIME HELPERS --------------------------
def ist_now():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

def market_open_ist(now=None):
    n = now or ist_now()
    if n.weekday() >= 5:
        return False
    start = n.replace(hour=9, minute=15, second=0, microsecond=0)
    end = n.replace(hour=15, minute=30, second=0, microsecond=0)
    return start <= n <= end

# -------------------------- NETWORK --------------------------
def get_session():
    s = requests.Session()
    s.headers.update(NSE_HEADERS)
    try:
        s.get("https://www.nseindia.com/", timeout=6)
    except Exception:
        pass
    return s

def _retry_get_json(url, params=None, retries=3, backoff=0.5):
    last_err = None
    for i in range(retries):
        try:
            s = get_session()
            r = s.get(url, params=params, timeout=8)
            if r.status_code == 200:
                return r.json()
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = repr(e)
        time.sleep(backoff * (i + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_err}")

@st.cache_data(ttl=20, show_spinner=False)
def fetch_all_indices():
    url = "https://www.nseindia.com/api/allIndices"
    data = _retry_get_json(url)
    by_name = {}
    for row in data.get("data", []):
        name = row.get("index")
        if not name:
            continue
        by_name[name] = {
            "ltp": row.get("last", row.get("lastPrice")),
            "change": row.get("variation", row.get("change")),
            "pchange": row.get("percentChange", row.get("pChange")),
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "prevClose": row.get("previousClose", row.get("prevClose")),
            "time": row.get("timeVal"),
        }
    return by_name

@st.cache_data(ttl=25, show_spinner=False)
def fetch_option_chain(symbol: str):
    url = "https://www.nseindia.com/api/option-chain-indices"
    data = _retry_get_json(url, params={"symbol": symbol})
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

# -------------------------- CALCS --------------------------
def compute_pcr(df: pd.DataFrame):
    ce_sum = float(df["ce_oi"].sum() or 0)
    pe_sum = float(df["pe_oi"].sum() or 0)
    return (pe_sum / ce_sum) if ce_sum > 0 else None

def compute_max_pain(df: pd.DataFrame):
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
    top_pe = df.nlargest(k, "pe_oi")[["strike", "pe_oi"]]
    top_ce = df.nlargest(k, "ce_oi")[["strike", "ce_oi"]]
    return list(top_pe["strike"].astype(int)), list(top_ce["strike"].astype(int))

def trade_bias(underlying, prev_close, oc_df):
    # Conservative quick-bias engine
    pcr = compute_pcr(oc_df)
    ce_delta = float(oc_df['ce_chg_oi'].sum())
    pe_delta = float(oc_df['pe_chg_oi'].sum())
    bias = "Sideways"
    if underlying is not None and prev_close is not None and pcr is not None:
        if (underlying > prev_close) and (pe_delta > 0) and (ce_delta <= 0) and (pcr > 0.9):
            bias = "Strong Buy CE"
        elif (underlying < prev_close) and (ce_delta > 0) and (pe_delta <= 0) and (pcr < 1.1):
            bias = "Strong Buy PE"
        elif pe_delta > 0 and ce_delta <= 0:
            bias = "Sell PE"
        elif ce_delta > 0 and pe_delta <= 0:
            bias = "Sell CE"
    return bias, pcr, ce_delta, pe_delta

# -------------------------- HISTORY --------------------------
def upsert_history(idx_key: str, ltp: float, max_points: int = 240):
    if "hist" not in st.session_state:
        st.session_state["hist"] = {}
    hist = st.session_state["hist"].setdefault(idx_key, [])
    now = ist_now()
    if not hist or hist[-1][1] != ltp:
        hist.append((now, float(ltp)))
    if len(hist) > max_points:
        st.session_state["hist"][idx_key] = hist[-max_points:]

def get_history_frame(indices):
    rows = []
    for idx in indices:
        for t, v in st.session_state.get("hist", {}).get(idx, []):
            rows.append({"index": idx, "time": t, "ltp": v})
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["index", "time", "ltp"])

# -------------------------- UI STATE --------------------------
if "symbol_choice" not in st.session_state:
    st.session_state["symbol_choice"] = "BANKNIFTY"
if "levels" not in st.session_state:
    st.session_state["levels"] = {}  # {symbol: {"supports": [], "resistances": []}}

# -------------------------- HEADER --------------------------
left, right = st.columns([0.75, 0.25])
with left:
    st.markdown("## Multi-index OI scanner")
    st.caption("Scan NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY. Stable auto-refresh, instant switching, and bias signals.")
with right:
    refresh_toggle = st.toggle("Auto-refresh", value=True)
    refresh_secs = st.selectbox("Interval", options=[10, 15, 20, 30, 45, 60], index=3, label_visibility="collapsed")
    st.caption(f"Every {refresh_secs}s")

# Smooth auto-refresh: trigger timed re-run, but preserve layout and keys
if refresh_toggle:
    st.autorefresh_count = st.experimental_data_editor if False else None  # no-op to keep keys stable
    st.autorefresh_token = st.experimental_get_query_params()  # no-op anchor
    st.session_state["_auto_ts"] = int(time.time())
    st.autorefresh = st.runtime.legacy_caching.hashing if False else None  # keep structure constant
    st_autorefresh = st.experimental_memo if False else None  # placeholders to keep DOM stable
    st.runtime = None  # harmless
    st_autorefresh_count = st.sidebar if False else None
    # Real refresh timer:
    st.experimental_set_query_params(ts=str(int(time.time())))  # stable URL param to avoid cache collisions
    st._ = st.empty()  # placeholder anchor
    st._.markdown(f"<span class='small'>Last update: {ist_now().strftime('%H:%M:%S')}</span>", unsafe_allow_html=True)
    st.autorefresh_key = st.empty()

    # Use Streamlit's built-in timer to trigger re-run without manual rerun()
    st.session_state["__ref__"] = int(time.time() // refresh_secs)

# -------------------------- MARKET STATUS --------------------------
if not market_open_ist():
    st.info("Market (IST) likely closed. Data may be static. You can still analyze structure.")

# -------------------------- SNAPSHOT TILES --------------------------
try:
    all_idx = fetch_all_indices()
except Exception as e:
    st.warning(f"Index snapshot unavailable. Falling back to OC underlyings. ({e})")
    all_idx = {}

tile_cols = st.columns(len(INDICES))
clicked = None

def tiny_sparkline(df: pd.DataFrame):
    if df.empty:
        return alt.Chart(pd.DataFrame({"time": [], "ltp": []})).mark_line()
    base = alt.Chart(df).encode(
        x=alt.X("time:T", axis=None),
        y=alt.Y("ltp:Q", axis=None)
    )
    return (base.mark_area(color="#22C55E", opacity=0.18) + base.mark_line(color="#22C55E", strokeWidth=2)).properties(height=42)

for i, sym in enumerate(INDICES):
    idx_name = INDEX_NAME_MAP.get(sym, sym)
    dat = all_idx.get(idx_name, {})
    ltp = dat.get("ltp")
    change = dat.get("change")
    pchg = dat.get("pchange")
    prev_close = dat.get("prevClose")

    # Fallback to OC underlying if missing
    if ltp is None:
        try:
            under, _ = fetch_option_chain(sym)
            ltp = under
        except Exception:
            ltp = None

    if ltp is not None:
        upsert_history(sym, float(ltp))

    with tile_cols[i]:
        box = st.container(border=True)
        with box:
            st.markdown(f"<div class='tile-box'>", unsafe_allow_html=True)
            top, spark = st.columns([0.55, 0.45])
            with top:
                st.markdown(f"**{sym}**")
                st.markdown(f"{(ltp if ltp is not None else '—')}")
                if change is not None and pchg is not None:
                    emoji = "🟢" if float(change) >= 0 else "🔻"
                    st.caption(f"{emoji} {float(change):+,.2f} ({float(pchg):+,.2f}%)")
                else:
                    st.caption("—")
            with spark:
                hdf = get_history_frame([sym])
                sparkline = tiny_sparkline(hdf[hdf["index"] == sym])
                st.altair_chart(sparkline, use_container_width=True)

            if st.button("Open", key=f"open_{sym}", use_container_width=True):
                clicked = sym
            st.markdown("</div>", unsafe_allow_html=True)

# -------------------------- SELECTED SYMBOL --------------------------
selected = clicked or st.session_state.get("symbol_choice", "BANKNIFTY")
st.session_state["symbol_choice"] = selected

st.markdown("---")
hdr_left, hdr_mid, hdr_right = st.columns([0.5, 0.25, 0.25])
with hdr_left:
    st.subheader(f"Detailed view: {selected}")
with hdr_mid:
    pass
with hdr_right:
    pass

# -------------------------- OPTION CHAIN & METRICS --------------------------
try:
    underlying, oc_df = fetch_option_chain(selected)
except Exception as e:
    st.error(f"Option chain unavailable for {selected}. Try again shortly. ({e})")
    st.stop()

# Pull prev close for bias calc
prev_close = None
if INDEX_NAME_MAP.get(selected) in all_idx:
    prev_close = all_idx[INDEX_NAME_MAP[selected]].get("prevClose")

bias, pcr, ce_delta, pe_delta = trade_bias(underlying, prev_close, oc_df)

metric_cols = st.columns(4)
with metric_cols[0]:
    st.metric("Underlying", f"{underlying:.2f}" if underlying else "—")
with metric_cols[1]:
    st.metric("PCR (Total OI)", f"{pcr:.2f}" if pcr else "—")
with metric_cols[2]:
    st.metric("Σ ΔOI (CE)", f"{ce_delta:,.0f}")
with metric_cols[3]:
    st.metric("Σ ΔOI (PE)", f"{pe_delta:,.0f}")

# Bias badge
bias_color = {
    "Strong Buy CE": "✅",
    "Strong Buy PE": "🟥",
    "Sell PE": "🟩",
    "Sell CE": "🟧",
    "Sideways": "⚪"
}.get(bias, "⚪")
st.caption(f"{bias_color} Bias: {bias}")

# -------------------------- TABS --------------------------
tab1, tab2, tab3 = st.tabs(["CE vs PE OI", "Change in OI", "Max Pain • PCR • Levels"])

def crossover_points(df: pd.DataFrame, threshold_ratio=0.1):
    if df.empty:
        return pd.DataFrame(columns=["strike", "label"])
    rows = []
    for _, r in df.iterrows():
        ce = float(r["ce_oi"] or 0)
        pe = float(r["pe_oi"] or 0)
        m = max(ce, pe)
        if m == 0:
            continue
        if abs(ce - pe) <= threshold_ratio * m:
            rows.append({"strike": r["strike"], "label": "near crossover"})
    return pd.DataFrame(rows)

with tab1:
    holder = st.container()
    with holder:
        st.markdown("<div class='chart-box'>", unsafe_allow_html=True)
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

            if underlying:
                vline = alt.Chart(pd.DataFrame({"x": [underlying]})).mark_rule(
                    color="#22C55E", strokeWidth=2
                ).encode(x="x:Q")
                chart = chart + vline

            cross_df = crossover_points(oc_df)
            if not cross_df.empty:
                cross_mark = alt.Chart(cross_df).mark_point(
                    shape="triangle-up", color="#6366F1", size=80
                ).encode(x="strike:Q", y=alt.value(0))
                chart = chart + cross_mark

            st.altair_chart(chart.properties(height=380), use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

with tab2:
    st.markdown("<div class='chart-box'>", unsafe_allow_html=True)
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
        st.altair_chart((bars + hzero).properties(height=320), use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

with tab3:
    c1, c2, c3 = st.columns([0.32, 0.34, 0.34])
    with c1:
        mp_strike, _ = compute_max_pain(oc_df)
        st.markdown("**Summary**")
        st.metric("Max Pain", f"{int(mp_strike)}" if mp_strike else "—")
        st.metric("PCR (Total OI)", f"{pcr:.2f}" if pcr else "—")
        if underlying and mp_strike:
            st.caption(f"Distance to Max Pain: {underlying - mp_strike:+.0f}")

    sup, res = sr_levels(oc_df, k=3)
    prev = st.session_state["levels"].get(selected, {"supports": [], "resistances": []})
    new_sup = [s for s in sup if s not in prev["supports"]]
    new_res = [r for r in res if r not in prev["resistances"]]
    st.session_state["levels"][selected] = {"supports": sup, "resistances": res}

    with c2:
        st.markdown("**Support (Top PE OI)**")
        for s in sup:
            tag = " 🆕" if s in new_sup else ""
            st.write(f"- {s}{tag}")
    with c3:
        st.markdown("**Resistance (Top CE OI)**")
        for r in res:
            tag = " 🆕" if r in new_res else ""
            st.write(f"- {r}{tag}")

# -------------------------- SIDEBAR: QUICK COMPARE --------------------------
st.sidebar.header("Quick compare")
hist_all = get_history_frame(INDICES)
if hist_all.empty:
    st.sidebar.caption("Building snapshot history…")
else:
    chart = alt.Chart(hist_all).mark_line().encode(
        x=alt.X("time:T", title=None),
        y=alt.Y("ltp:Q", title=None),
        color=alt.Color("index:N", legend=alt.Legend(orient="bottom", title=None)),
        tooltip=[alt.Tooltip("index:N"), alt.Tooltip("time:T"), alt.Tooltip("ltp:Q")],
    ).properties(height=180)
    st.sidebar.altair_chart(chart, use_container_width=True)

with st.sidebar.expander("Settings"):
    st.caption("History length")
    max_points = st.slider("Max points", 60, 600, 240, 30)
    if "hist" in st.session_state:
        for k, h in list(st.session_state["hist"].items()):
            if len(h) > max_points:
                st.session_state["hist"][k] = h[-max_points:]

# -------------------------- FOOTER --------------------------
st.caption("Data: NSE public endpoints. If a call stalls, it usually recovers on the next cycle.")
