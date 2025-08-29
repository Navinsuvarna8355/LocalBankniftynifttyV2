# new_app.py
import streamlit as st
import time
import pandas as pd
import math
import json
import requests
import logging

# Set up logging to show debug information
logging.basicConfig(level=logging.INFO)

# --- Web Scraping and Calculation Functions ---
def fetch_option_chain(symbol='BANKNIFTY'):
    """
    Fetches live option chain data from NSE with improved headers and session management.
    """
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    
    headers = {
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'en-US,en;q=0.9',
        'Upgrade-Insecure-Requests': '1',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
        'X-Requested-With': 'XMLHttpRequest',
    }
    
    session = requests.Session()
    session.headers.update(headers)
    
    try:
        logging.info(f"Fetching cookies from NSE...")
        # First request to get cookies
        session.get("https://www.nseindia.com", timeout=10)
        
        logging.info(f"Fetching option chain for {symbol}...")
        # Second request to fetch the option chain data
        response = session.get(url, timeout=10)
        response.raise_for_status()  # Raise an exception for bad status codes
        
        logging.info("Data fetched successfully.")
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching data from NSE: {e}")
        raise Exception(f"Failed to fetch data from NSE. Error: {e}")

def compute_oi_pcr_and_underlying(data):
    """
    Computes PCR and gets underlying price from the fetched data.
    """
    if not data or 'records' not in data or 'data' not in data['records']:
        return {'underlying': None, 'pcr_total': None, 'pcr_near': None, 'expiry': None}

    expiry_dates = data['records']['expiryDates']
    if not expiry_dates:
        raise ValueError("No expiry dates found in the data.")
        
    current_expiry = expiry_dates[0]
    
    pe_total_oi = 0
    ce_total_oi = 0
    pe_near_oi = 0
    ce_near_oi = 0

    underlying_price = data['records']['underlyingValue']
    
    for item in data['records']['data']:
        pe_total_oi += item.get('PE', {}).get('openInterest', 0)
        ce_total_oi += item.get('CE', {}).get('openInterest', 0)
        
        # Check for near expiry data
        if item.get('expiryDate') == current_expiry:
            pe_near_oi += item.get('PE', {}).get('openInterest', 0)
            ce_near_oi += item.get('CE', {}).get('openInterest', 0)

    pcr_total = pe_total_oi / ce_total_oi if ce_total_oi != 0 else math.inf
    pcr_near = pe_near_oi / ce_near_oi if ce_near_oi != 0 else math.inf

    return {
        'underlying': underlying_price,
        'pcr_total': pcr_total,
        'pcr_near': pcr_near,
        'expiry': current_expiry
    }

# --- Strategy and UI Functions ---
def determine_signal(pcr, trend, ema_signal):
    """
    Based on PCR, trend and EMA signal, determines the final trading signal.
    """
    signal = "SIDEWAYS"
    suggested_option = None

    if trend == "BULLISH" and ema_signal == "BUY" and pcr >= 1:
        signal = "BUY"
        suggested_option = "CALL"
    elif trend == "BEARISH" and ema_signal == "SELL" and pcr <= 1:
        signal = "SELL"
        suggested_option = "PUT"
    else:
        signal = "SIDEWAYS"
        suggested_option = None
    return signal, suggested_option

def display_dashboard(symbol, info):
    """
    Displays the dashboard for a given symbol.
    """
    st.header(f"{symbol} Live Analysis")
    st.divider()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Live Price", f"₹ {info['underlying']:.2f}")
    with col2:
        st.metric("Total PCR", f"{info['pcr_total']:.2f}")
    with col3:
        st.metric("Near PCR", f"{info['pcr_near']:.2f}")

    st.subheader("Strategy Signal")
    
    ema_signal_choice = st.radio(
        "Select EMA Signal",
        ["BUY", "SELL"],
        index=0,
        horizontal=True,
        help="Select 'BUY' for bullish EMA crossover or 'SELL' for bearish."
    )
    
    use_near_pcr = st.checkbox("Use Near Expiry PCR?", value=True)
    
    pcr_used = info['pcr_near'] if use_near_pcr else info['pcr_total']
    trend = "BULLISH" if pcr_used >= 1 else "BEARISH"
    
    signal, suggested_side = determine_signal(pcr_used, trend, ema_signal_choice)
    
    st.write(f"**Used PCR**: {pcr_used:.2f} ({'Near Expiry' if use_near_pcr else 'Total OI'})")
    st.write(f"**Trend**: {trend}")

    if signal == "BUY":
        st.success(f"Signal: {signal} ({suggested_side}) - At-The-Money option suggested: ₹{round(info['underlying']/100)*100} CE")
    elif signal == "SELL":
        st.error(f"Signal: {signal} ({suggested_side}) - At-The-Money option suggested: ₹{round(info['underlying']/100)*100} PE")
    else:
        st.info("Signal: SIDEWAYS - No strong signal found.")
        
    st.divider()
    
    st.write(f"Last updated: {info['last_update']}")
    st.write("Data source: NSE India")
    st.warning("Disclaimer: This is for educational purposes only. Do not use for live trading.")

def main():
    """
    Main function to run the Streamlit app.
    """
    st.set_page_config(
        page_title="NSE Option Chain Strategy",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    
    st.title("NSE Option Chain Analysis Dashboard")
    st.markdown("This dashboard provides live analysis of NIFTY and BANKNIFTY based on a custom trading strategy.")

    symbol_choice = st.sidebar.radio(
        "Select Symbol",
        ["NIFTY", "BANKNIFTY"],
        index=0
    )
    
    dashboard_placeholder = st.empty()

    while True:
        try:
            with st.spinner(f"Fetching live data for {symbol_choice}... Please wait."):
                data = fetch_option_chain(symbol_choice)
                info = compute_oi_pcr_and_underlying(data)
            
            info['last_update'] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

            with dashboard_placeholder.container():
                display_dashboard(symbol_choice, info)

        except Exception as e:
            st.error(f"Error fetching data for {symbol_choice}: {e}")
            st.info("Retrying in 5 seconds...")

        time.sleep(5)

if __name__ == "__main__":
    main()
