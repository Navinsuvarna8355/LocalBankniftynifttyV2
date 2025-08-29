# app.py
# Requirements: streamlit==1.36.0 pandas requests altair

import streamlit as st
import pandas as pd
import altair as alt
import requests, time
from datetime import datetime, timedelta

# ---------------- CONFIG ----------------
st.set_page_config(page_title="Multi‑Index OI Dashboard", layout="wide")
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

# ---------------- TIME ----------------
def ist_now(): return datetime.utcnow() + timedelta(hours=5, minutes=30)
def market_open():
    n=ist_now()
    return n.weekday()<5 and n.replace(hour=9,minute=15) <= n <= n.replace(hour=15,minute=30)

# ---------------- FETCH ----------------
def get_session():
    s=requests.Session(); s.headers.update(HEADERS)
    try: s.get("https://www.nseindia.com/",timeout=5)
    except: pass
    return s

def get_json(url, params=None):
    for _ in range(3):
        try:
            r=get_session().get(url,params=params,timeout=8)
            if r.status_code==200: return r.json()
        except: time.sleep(0.5)
    return {}

@st.cache_data(ttl=20)
def fetch_all_indices():
    url="https://www.nseindia.com/api/allIndices"
    data=get_json(url); out={}
    for row in data.get("data",[]):
        nm=row.get("index"); 
        if nm:
            out[nm]={"ltp":row.get("last") or row.get("lastPrice"),
                     "change":row.get("change"),"pchange":row.get("pChange"),
                     "prevClose":row.get("previousClose")}
    return out

@st.cache_data(ttl=25)
def fetch_oc(symbol):
    url="https://www.nseindia.com/api/option-chain-indices"
    data=get_json(url,params={"symbol":symbol})
    rec=data.get("records",{})
    under=rec.get("underlyingValue")
    rows=rec.get("data",[])
    df=pd.DataFrame([{
        "strike":r.get("strikePrice"),
        "ce_oi":r.get("CE",{}).get("openInterest",0),
        "pe_oi":r.get("PE",{}).get("openInterest",0),
        "ce_chg_oi":r.get("CE",{}).get("changeinOpenInterest",0),
        "pe_chg_oi":r.get("PE",{}).get("changeinOpenInterest",0),
    } for r in rows]).dropna(subset=["strike"]).sort_values("strike")
    return under, df

# ---------------- CALCS ----------------
def pcr(df): ce=df["ce_oi"].sum(); pe=df["pe_oi"].sum(); return pe/ce if ce else None
def max_pain(df):
    strikes=df["strike"].astype(float); ce=df["ce_oi"].astype(float); pe=df["pe_oi"].astype(float)
    pains=[((strikes-s).clip(0)*ce).sum()+((s-strikes).clip(0)*pe).sum() for s in strikes]
    return strikes[pains.index(min(pains))] if pains else None
def sr(df,k=3):
    return list(df.nlargest(k,"pe_oi")["strike"]), list(df.nlargest(k,"ce_oi")["strike"])
def bias(under, prev, df):
    ce_d=df["ce_chg_oi"].sum(); pe_d=df["pe_chg_oi"].sum(); pc=pcr(df)
    if under and prev:
        if under>prev and pe_d>0 and ce_d<=0 and pc>0.9: return "Strong Buy CE"
        if under<prev and ce_d>0 and pe_d<=0 and pc<1.1: return "Strong Buy PE"
        if pe_d>0 and ce_d<=0: return "Sell PE"
        if ce_d>0 and pe_d<=0: return "Sell CE"
    return "Sideways"

# ---------------- STATE ----------------
if "symbol" not in st.session_state: st.session_state.symbol="BANKNIFTY"
if "hist" not in st.session_state: st.session_state.hist={}
if "levels" not in st.session_state: st.session_state.levels={}

def update_hist(sym, ltp):
    hist=st.session_state.hist.setdefault(sym,[])
    if not hist or hist[-1][1]!=ltp: hist.append((ist_now(),ltp))
    if len(hist)>300: st.session_state.hist[sym]=hist[-300:]

def hist_df(sym): 
    h=st.session_state.hist.get(sym,[])
    return pd.DataFrame(h,columns=["time","ltp"])

# ---------------- HEADER ----------------
c1,c2=st.columns([0.75,0.25])
with c1: st.markdown("## Multi‑Index OI Scanner"); st.caption("Bias engine • S/R alerts • Smooth refresh")
with c2: auto=st.toggle("Auto-refresh",True); secs=st.selectbox("Interval (s)",[10,20,30,60],1,label_visibility="collapsed")
if not market_open(): st.info("Market closed (IST) — data may be static.")

# ---------------- TILES ----------------
all_idx=fetch_all_indices()
tcols=st.columns(len(INDICES)); click=None
for i,sym in enumerate(INDICES):
    nm=INDEX_NAME_MAP[sym]; dat=all_idx.get(nm,{})
    ltp=dat.get("ltp"); prev=dat.get("prevClose")
    if ltp: update_hist(sym,ltp)
    with tcols[i]:
        st.markdown(f"**{sym}**")
        st.write(ltp or "—")
        if dat.get("change") is not None:
            emo="🟢" if dat["change"]>=0 else "🔻"
            st.caption(f"{emo} {dat['change']:+.2f} ({dat['pchange']:+.2f}%)")
        if st.button("Open",key=f"btn_{sym}_{i}"): click=sym

st.session_state.symbol=click or st.session_state.symbol
sel=st.session_state.symbol
st.markdown(f"---\n### {sel}")

# ---------------- DETAIL ----------------
under,df=fetch_oc(sel)
prev=all_idx.get(INDEX_NAME_MAP[sel],{}).get("prevClose")
b=bias(under,prev,df); pc=pcr(df); mp=max_pain(df)
st.caption(f"Bias: {b} | PCR: {pc or '—'} | Max Pain: {mp or '—'}")

tab1,tab2,tab3=st.tabs(["CE vs PE OI","Δ OI","Levels"])

with tab1:
    if not df.empty:
        long=df.melt(id_vars=["strike"],value_vars=["ce_oi","pe_oi"],var_name="type",value_name="oi")
        long["type"]=long["type"].map({"ce_oi":"CE","pe_oi":"PE"})
        st.altair_chart(alt.Chart(long).mark_bar().encode(x="strike:Q",y="oi:Q",color="type"),use_container_width=True)

with tab2:
    if not df.empty:
        long=df.melt(id_vars=["strike"],value_vars=["ce_chg_oi","pe_chg_oi"],var_name="type",value_name="chg")
        long["type"]=long["type"].map({"ce_chg_oi":"CE ΔOI","pe_chg_oi":"PE ΔOI"})
        st.altair_chart(alt.Chart(long).mark_bar().encode(x="strike:Q",y="chg:Q",color="type"),use_container_width=True)

with tab3:
    sup,res=sr(df)
    prev_levels=st.session_state.levels.get(sel,{"sup":[],"res":[]})
    new_sup=[s for s in sup if s not in prev_levels["sup"]]
    new_res=[r for r in res if r
