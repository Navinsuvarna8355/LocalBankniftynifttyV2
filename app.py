# app.py
# Requirements:
#   streamlit==1.36.0
#   pandas==2.2.2
#   altair==5.3.0
#   requests==2.32.3
#
# Run:
#   pip install -r <(printf "streamlit==1.36.0\npandas==2.2.2\naltair==5.3.0\nrequests==2.32.3\n")
#   streamlit run app.py

import time
from datetime import datetime, timedelta

import altair as alt
import pandas as pd
import requests
import streamlit as st

# ---------------- CONFIG ----------------
st.set_page_config(page_title="Multi-Index OI Dashboard", layout="wide")
alt.themes.enable("opaque")

INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
INDEX_NAME_MAP = {
    "NIFTY": "NIFTY 50",
    "BANKNIFTY": "NIFTY BANK",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NIFTY MIDCAP SELECT",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}

# ---------------- UTIL ----------------
def ist_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

def market_open() -> bool:
    n = ist_now()
    return n.weekday() < 5 and n.replace(hour=9, minute=15, second=0, microsecond=0) <= n <= n.replace(hour=15, minute=30, second=0, microsecond=0)

def fmt(x, nd=2):
    if x is None:
        return "—"
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return str(x)

# ---------------- HTTP ----------------
@st.cache_resource(show_spinner=False)
def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get("https://www.nseindia.com/", timeout=6)
    except Exception:
        pass
    return s

def get_json(url, params=None):
    s = get_session()
    for _ in range(3):
        try:
            r = s.get(url, params=params, timeout=8)
            if r.status_code == 200:
                return r.json()
        except Exception:
            time.sleep(0.4)
    return {}

# ---------------- DATA ----------------
@st.cache_data(ttl=20, show_spinner=False)
def fetch_all_indices():
    url = "https://www.nseindia.com/api/allIndices"
    data = get_json(url)
    out = {}
    for row in data.get("data", []):
        nm = row.get("index")
        if not nm:
            continue
        out[nm] = {
            "ltp": row.get("last") or row.get("lastPrice"),
            "change": row.get("change"),
            "pchange": row.get("pChange"),
            "prevClose": row.get("previousClose"),
        }
    return out

@st.cache_data(ttl=25, show_spinner=False)
def fetch_option_chain(symbol: str):
    url = "https://www.nseindia.com/api/option-chain-indices"
    data = get_json(url, params={"symbol": symbol})
    rec = data.get("records", {})
    under = rec.get("underlyingValue")
    rows = rec.get("data", [])
    if not rows:
        return under, pd.DataFrame(columns=["strike","ce_oi","pe_oi","ce_chg_oi","pe_chg_oi"])
    df = pd.DataFrame([
        {
            "strike": r.get("strikePrice"),
            "ce_oi": (r.get("CE") or {}).get("openInterest", 0) or 0,
            "pe_oi": (r.get("PE") or {}).get("openInterest", 0) or 0,
            "ce_chg_oi": (r.get("CE") or {}).get("changeinOpenInterest", 0) or 0,
            "pe_chg_oi": (r.get("PE") or {}).get("changeinOpenInterest", 0) or 0,
        }
        for r in rows
    ])
    df = df.dropna(subset=["strike"]).sort_values("strike").reset_index(drop=True)
    return under, df

# ---------------- METRICS ----------------
def pcr(df: pd.DataFrame):
    if df.empty:
        return None
    ce = float(df["ce_oi"].sum())
    pe = float(df["pe_oi"].sum())
    return (pe / ce) if ce else None

def max_pain(df: pd.DataFrame):
    if df.empty:
        return None
    strikes = df["strike"].astype(float).values.tolist()
    ce = df["ce_oi"].astype(float).values
    pe = df["pe_oi"].astype(float).values
    pains = []
    for s in strikes:
        pain = ((df["strike"].astype(float) - s).clip(lower=0).values * ce).sum() + ((s - df["strike"].astype(float)).clip(lower=0).values * pe).sum()
        pains.append(pain)
    if not pains:
        return None
    idx = pains.index(min(pains))
    return strikes[idx]

def sr_levels(df: pd.DataFrame, k=3):
    if df.empty:
        return [], []
    sup = df.nlargest(k, "pe_oi")["strike"].astype(float).tolist()
    res = df.nlargest(k, "ce_oi")["strike"].astype(float).tolist()
    return sup, res

def bias_engine(under, prev, df: pd.DataFrame):
    if df.empty:
        return "Sideways"
    ce_d = float(df["ce_chg_oi"].sum())
    pe_d = float(df["pe_chg_oi"].sum())
    pc = pcr(df) or 0
    if under and prev:
        if under > prev and pe_d > 0 and ce_d <= 0 and pc > 0.9:
            return "Strong Buy CE"
        if under < prev and ce_d > 0 and pe_d <= 0 and pc < 1.1:
            return "Strong Buy PE"
        if pe_d > 0 and ce_d <= 0:
            return "Sell PE"
        if ce_d > 0 and pe_d <= 0:
            return "Sell CE"
    return "Sideways"

# ---------------- STATE ----------------
if "symbol" not in st.session_state:
    st.session_state.symbol = "BANKNIFTY"
if "hist" not in st.session_state:
    st.session_state.hist = {}  # {sym: [(ts, ltp), ...]}
if "levels" not in st.session_state:
    st.session_state.levels = {}  # {sym: {"sup": [], "res": []}}

def update_hist(sym: str, ltp):
    if ltp is None:
        return
    hist = st.session_state.hist.setdefault(sym, [])
    if not hist or hist[-1][1] != ltp:
        hist.append((ist_now(), float(ltp)))
    if len(hist) > 300:
        st.session_state.hist[sym] = hist[-300:]

def hist_df(sym: str) -> pd.DataFrame:
    h = st.session_state.hist.get(sym, [])
    if not h:
        return pd.DataFrame(columns=["time", "ltp"])
    return pd.DataFrame(h, columns=["time", "ltp"])

# ---------------- HEADER ----------------
left, right = st.columns([0.75, 0.25])
with left:
    st.markdown("## Multi-Index OI Scanner")
    st.caption("Bias engine • S/R alerts • Smooth refresh")

with right:
    auto = st.toggle("Auto-refresh", True)
    secs = st.selectbox("Interval (s)", [10, 20, 30, 60], index=1, key="refresh_interval")
    # Lightweight client-side refresh to keep layout stable
    if auto:
        st.markdown(
            f"<meta http-equiv='refresh' content='{int(secs)}'>",
            unsafe_allow_html=True,
        )

if not market_open():
    st.info("Market closed (IST) — data may be static.")

# ---------------- TILES ----------------
all_idx = fetch_all_indices()
tcols = st.columns(len(INDICES))
clicked = None

for i, sym in enumerate(INDICES):
    nm = INDEX_NAME_MAP[sym]
    dat = all_idx.get(nm, {}) or {}
    ltp = dat.get("ltp")
    prev = dat.get("prevClose")
    chg = dat.get("change")
    pchg = dat.get("pchange")
    if ltp is not None:
        update_hist(sym, ltp)

    with tcols[i]:
        st.markdown(f"**{sym}**")
        st.write(fmt(ltp))
        if chg is not None and pchg is not None:
            emo = "🟢" if float(chg) >= 0 else "🔻"
            st.caption(f"{emo} {fmt(chg)} ({fmt(pchg)}%)")

        # sparkline
        hdf = hist_df(sym)
        if not hdf.empty:
            line = (
                alt.Chart(hdf)
                .mark_line()
                .encode(
                    x=alt.X("time:T", axis=None),
                    y=alt.Y("ltp:Q", axis=None),
                )
                .properties(height=40)
            )
            st.altair_chart(line, use_container_width=True)

        if st.button("Open", key=f"open_{sym}"):
            clicked = sym

if clicked:
    st.session_state.symbol = clicked

sel = st.session_state.symbol
st.markdown("---")
st.markdown(f"### {sel}")

# ---------------- DETAIL ----------------
under, df = fetch_option_chain(sel)
idx_info = all_idx.get(INDEX_NAME_MAP[sel], {}) or {}
prev_close = idx_info.get("prevClose")

bias = bias_engine(under, prev_close, df)
pc = pcr(df)
mp = max_pain(df)

m1, m2, m3, m4, m5 = st.columns(5)
with m1:
    st.metric("Underlying", fmt(under))
with m2:
    st.metric("Prev Close", fmt(prev_close))
with m3:
    ch = idx_info.get("change")
    ch_pct = idx_info.get("pchange")
    st.metric("Change", f"{fmt(ch)} ({fmt(ch_pct)}%)" if (ch is not None and ch_pct is not None) else "—")
with m4:
    st.metric("PCR", fmt(pc, 2))
with m5:
    st.metric("Max Pain", fmt(mp, 0))

st.caption(f"Bias: {bias}")

tab1, tab2, tab3 = st.tabs(["CE vs PE OI", "Δ OI", "Levels"])

with tab1:
    if df.empty:
        st.warning("No option chain data available.")
    else:
        long = df.melt(
            id_vars=["strike"],
            value_vars=["ce_oi", "pe_oi"],
            var_name="type",
            value_name="oi",
        )
        long["type"] = long["type"].map({"ce_oi": "CE", "pe_oi": "PE"})
        chart = (
            alt.Chart(long)
            .mark_bar()
            .encode(
                x=alt.X("strike:Q", title="Strike"),
                y=alt.Y("oi:Q", title="Open Interest"),
                color=alt.Color("type:N", scale=alt.Scale(scheme="tableau10")),
                tooltip=["type", "strike", "oi"],
            )
            .properties(height=340)
        )
        st.altair_chart(chart, use_container_width=True)

with tab2:
    if df.empty:
        st.warning("No option chain data available.")
    else:
        long = df.melt(
            id_vars=["strike"],
            value_vars=["ce_chg_oi", "pe_chg_oi"],
            var_name="type",
            value_name="chg",
        )
        long["type"] = long["type"].map({"ce_chg_oi": "CE ΔOI", "pe_chg_oi": "PE ΔOI"})
        chart = (
            alt.Chart(long)
            .mark_bar()
            .encode(
                x=alt.X("strike:Q", title="Strike"),
                y=alt.Y("chg:Q", title="Change in OI"),
                color=alt.Color("type:N", scale=alt.Scale(scheme="tableau10")),
                tooltip=["type", "strike", "chg"],
            )
            .properties(height=340)
        )
        st.altair_chart(chart, use_container_width=True)

with tab3:
    if df.empty:
        st.info("Awaiting data to compute S/R levels.")
    else:
        sup, res = sr_levels(df)
        prev_levels = st.session_state.levels.get(sel, {"sup": [], "res": []})
        new_sup = [s for s in sup if s not in prev_levels["sup"]]
        new_res = [r for r in res if r not in prev_levels["res"]]

        cols = st.columns(2)
        with cols[0]:
            st.markdown("**Support (PE OI):**")
            st.write(", ".join(fmt(s, 0) for s in sup) if sup else "—")
        with cols[1]:
            st.markdown("**Resistance (CE OI):**")
            st.write(", ".join(fmt(r, 0) for r in res) if res else "—")

        if new_sup or new_res:
            st.warning(f"📈 New S/R detected — Support: {', '.join(fmt(s,0) for s in new_sup) or '—'} | Resistance: {', '.join(fmt(r,0) for r in new_res) or '—'}")

        # persist latest levels for change detection on next refresh
        st.session_state.levels[sel] = {"sup": sup, "res": res}

# ---------------- FOOTER ----------------
st.caption("Data: NSE public endpoints • This is for informational purposes only.")
