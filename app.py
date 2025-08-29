import streamlit as st
import time
import requests
import json
import logging
import math

# Set up logging to show debug information
logging.basicConfig(level=logging.INFO)

# --- Web Scraping and Calculation Functions ---
def fetch_option_chain(symbol='BANKNIFTY', max_retries=3, delay=5):
    """
    Fetches live option chain data from NSE with improved headers and a retry mechanism
    that first establishes a session.
    
    Args:
        symbol (str): The stock index symbol (e.g., 'BANKNIFTY').
        max_retries (int): Maximum number of retries for a failed request.
        delay (int): Delay in seconds between retries.
    """
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'en-US,en;q=0.9',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': 'https://www.nseindia.com/option-chain',
        'Connection': 'keep-alive',
        'Host': 'www.nseindia.com',
    }
    
    session = requests.Session()
    session.headers.update(headers)
    
    for attempt in range(max_retries):
        try:
            # Step 1: Establish a session and get cookies from the home page
            logging.info(f"Attempt {attempt + 1}: Establishing session with NSE...")
            session.get("https://www.nseindia.com", timeout=10)
            
            # Step 2: Use the same session to fetch the option chain data
            logging.info(f"Attempt {attempt + 1}: Fetching option chain for {symbol}...")
            response = session.get(url, timeout=10)
            response.raise_for_status()  # Raise exception for bad status codes
            
            data = response.json()
            logging.info("Data fetched and parsed successfully.")
            return data
            
        except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
            logging.error(f"Attempt {attempt + 1} failed for {symbol}: {e}")
            if attempt < max_retries - 1:
                logging.info(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                raise Exception(f"Failed to fetch data after {max_retries} attempts. Error: {e}")

def compute_oi_pcr_and_underlying(data):
    """
    Computes PCR and gets underlying price from the fetched data.
    """
    if not data or 'records' not in data or 'data' not in data['records']:
        raise ValueError("Invalid data format from NSE.")

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

def display_dashboard():
    """
    Displays the dashboard for a given symbol using session state.
    """
    symbol_choice = st.session_state.symbol
    info = st.session_state.info
    
    st.header(f"{symbol_choice} Live Analysis")
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
        key='ema_signal_choice',
        help="Select 'BUY' for bullish EMA crossover or 'SELL' for bearish."
    )
    
    use_near_pcr = st.checkbox("Use Near Expiry PCR?", value=True, key='use_near_pcr')
    
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
    
    st.write(f"Last updated: {st.session_state.last_update}")
    st.write("Data source: NSE India")
    st.warning("Disclaimer: This is for educational purposes only. Do not use for live trading.")

def fetch_data():
    """Fetches data and updates the session state."""
    st.session_state.loading = True
    try:
        data = fetch_option_chain(st.session_state.symbol)
        st.session_state.info = compute_oi_pcr_and_underlying(data)
        st.session_state.last_update = time.strftime("%Y-%m-%d %H:%M:%S")
        st.session_state.error = None
    except Exception as e:
        st.session_state.error = str(e)
    finally:
        st.session_state.loading = False
        
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
    
    # Initialize session state variables if they don't exist
    if 'symbol' not in st.session_state:
        st.session_state.symbol = 'NIFTY'
    if 'info' not in st.session_state:
        st.session_state.info = None
    if 'error' not in st.session_state:
        st.session_state.error = None
    if 'loading' not in st.session_state:
        st.session_state.loading = False
    if 'last_update' not in st.session_state:
        st.session_state.last_update = "N/A"

    st.title("NSE Option Chain Analysis Dashboard")
    st.markdown("This dashboard provides live analysis of NIFTY and BANKNIFTY based on a custom trading strategy.")

    # Sidebar for symbol selection
    st.sidebar.header("Settings")
    symbol_choice = st.sidebar.radio(
        "Select Symbol",
        ["NIFTY", "BANKNIFTY"],
        index=["NIFTY", "BANKNIFTY"].index(st.session_state.symbol),
        key='symbol_radio'
    )
    
    # Update symbol in session state if the radio button changes
    if symbol_choice != st.session_state.symbol:
        st.session_state.symbol = symbol_choice
        # Trigger a data fetch when the symbol changes
        fetch_data()
        st.rerun()

    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("Refresh Data", use_container_width=True):
            fetch_data()
            st.rerun()

    # Display content based on app state
    if st.session_state.loading:
        st.info(f"Fetching live data for {st.session_state.symbol}... Please wait.")
    elif st.session_state.error:
        st.error(st.session_state.error)
        st.info("Click the 'Refresh Data' button to try again.")
    elif st.session_state.info:
        display_dashboard()
        
    st.sidebar.divider()
    st.sidebar.write("Last updated: " + st.session_state.last_update)
    st.sidebar.warning("Disclaimer: This is for educational purposes only. Do not use for live trading.")
    
if __name__ == "__main__":
    main()
