import streamlit as st
import pandas as pd
import math
import requests
import logging
import json
import time
from datetime import datetime

# Logging setup
logging.basicConfig(level=logging.INFO)

# --- Data Fetching Functions ---
def fetch_data_from_url(url, max_retries=5, delay=5):
    """
    Fetches data from a given URL using a requests.Session to maintain state
    and bypass security measures.
    
    Args:
        url (str): The URL to fetch data from.
        max_retries (int): Maximum number of retries for a failed request.
        delay (int): Delay in seconds between retries.
    """
    headers = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'hi-IN,hi;q=0.9,en-US;q=0.8,en;q=0.7',
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
            # Step 1: Initial request to get cookies from the home page.
            logging.info(f"Attempt {attempt + 1}: Establishing session with NSE option chain page...")
            session.get("https://www.nseindia.com/option-chain", timeout=10)
            
            # Step 2: Use the same session to fetch the data.
            logging.info(f"Attempt {attempt + 1}: Fetching data from {url}...")
            response = session.get(url, timeout=10)
            response.raise_for_status()  # Raise exception for bad status codes
            
            data = response.json()
            logging.info("Data fetched and parsed successfully.")
            return data
            
        except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
            logging.error(f"Attempt {attempt + 1} failed for {url}: {e}")
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

def fetch_india_vix():
    """Fetches India VIX data from NSE and handles errors gracefully."""
    vix_url = "https://www.nseindia.com/api/all-indices"
    try:
        data = fetch_data_from_url(vix_url, max_retries=3, delay=5)
        if data and 'data' in data:
            for index in data.get('data', []):
                if index.get('index') == 'INDIA VIX':
                    return index.get('lastPrice')
    except Exception as e:
        logging.error(f"Error fetching India VIX data: {e}")
    return None

# --- Strategy and UI Functions ---
def determine_trend(pcr):
    """
    Determines market trend based on PCR value.
    """
    if pcr >= 1.2:
        return "BULLISH"
    elif pcr <= 0.8:
        return "BEARISH"
    else:
        return "SIDEWAYS"

def get_vix_label(vix_value):
    """
    Returns a volatility label and advice based on the VIX value.
    """
    if vix_value is None:
        return {"value": 0.00, "label": "Not Available", "advice": "Volatility data is not available."}
    if vix_value < 15:
        return {"value": vix_value, "label": "Low Volatility", "advice": "The market has low volatility. Large price swings are not expected."}
    elif 15 <= vix_value <= 25:
        return {"value": vix_value, "label": "Medium Volatility", "advice": "The market has medium volatility. You can trade according to your strategy."}
    else:
        return {"value": vix_value, "label": "High Volatility", "advice": "The market has very high volatility. Trade with great caution or avoid trading."}

def display_dashboard(symbol, info, vix_data):
    """
    Displays the dashboard for a given symbol.
    """
    st.markdown("""
        <style>
            .main-container {
                padding: 2rem;
                border-radius: 0.75rem;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            }
            .card {
                background-color: #e5e7eb; /* Corresponds to gray-200 */
                padding: 1rem;
                border-radius: 0.5rem;
                text-align: center;
                color: #1f2937; /* Add this line for dark text color */
            }
            .blue-card {
                background-color: #dbeafe; /* Corresponds to blue-100 */
                color: #1f2937; /* Add this line for dark text color */
            }
            .signal-card {
                background-color: #f9fafb; /* Corresponds to gray-50 */
                padding: 1.5rem;
                border-radius: 0.5rem;
                text-align: center;
            }
            .signal-text {
                font-size: 1.5rem;
                font-weight: bold;
            }
            .green-text { color: #22c55e; } /* green-500 */
            .red-text { color: #ef4444; } /* red-500 */
            .yellow-text { color: #eab308; } /* yellow-500 */
        </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="main-container">', unsafe_allow_html=True)
    
    st.subheader(f"{symbol} Option Chain Dashboard")
    st.markdown("---")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f'<div class="card blue-card">Live Price<div style="font-size:1.5rem; font-weight: bold;">₹ {info["underlying"]:.2f}</div></div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="card">PCR<div style="font-size:1.5rem; font-weight: bold;">{info["pcr_total"]:.2f}</div></div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="card">Trend<div style="font-size:1.5rem; font-weight: bold;">{info["trend"]}</div></div>', unsafe_allow_html=True)
    with col4:
        st.markdown(f'<div class="card">India VIX<div style="font-size:1.5rem; font-weight: bold;">{vix_data["value"]:.2f}</div><div style="font-size:0.8rem;">{vix_data["label"]}</div></div>', unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("Market Volatility Advice")
    st.info(vix_data["advice"])
    st.markdown("---")

    st.subheader("Strategy Signal")
    
    if info['trend'] == "BULLISH":
        st.success(f"Signal: BUY (CALL)")
    elif info['trend'] == "BEARISH":
        st.error(f"Signal: SELL (PUT)")
    else:
        st.info("Signal: SIDEWAYS - No strong signal found.")
        
    st.divider()
    
    st.write(f"Data source: NSE India | Last Updated: {info['last_update']}")
    st.warning("Disclaimer: This is for educational purposes only. Do not use for live trading.")

    st.markdown('</div>', unsafe_allow_html=True)
    
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
    st.markdown("यह डैशबोर्ड NSE से लाइव डेटा का उपयोग करके NIFTY और BANKNIFTY का विश्लेषण प्रदान करता है।")

    # Initialize session state for the data
    if 'nifty_data' not in st.session_state:
        st.session_state.nifty_data = None
    if 'banknifty_data' not in st.session_state:
        st.session_state.banknifty_data = None
    
    symbol_choice = st.radio(
        "Select Symbol",
        ["NIFTY", "BANKNIFTY"],
        index=0,
        horizontal=True
    )

    refresh_button = st.button("Refresh Data")
    
    if refresh_button:
        try:
            with st.spinner(f"{symbol_choice} के लिए लाइव डेटा लाया जा रहा है... कृपया प्रतीक्षा करें।"):
                data = fetch_data_from_url(f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol_choice}")
                info = compute_oi_pcr_and_underlying(data)
                vix_value = fetch_india_vix()
            
            vix_data = get_vix_label(vix_value)
            
            info['trend'] = determine_trend(info['pcr_total'])
            info['last_update'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            info['vix_data'] = vix_data

            if symbol_choice == 'NIFTY':
                st.session_state.nifty_data = info
            elif symbol_choice == 'BANKNIFTY':
                st.session_state.banknifty_data = info
            
            st.success("डेटा सफलतापूर्वक अपडेट हो गया है!")
            
        except Exception as e:
            st.error(f"डेटा लाने में त्रुटि हुई: {e}")
            st.info("कृपया 'Refresh Data' पर क्लिक करके फिर से प्रयास करें।")

    if symbol_choice == 'NIFTY' and st.session_state.nifty_data:
        info = st.session_state.nifty_data
        display_dashboard(symbol_choice, info, info['vix_data'])
    elif symbol_choice == 'BANKNIFTY' and st.session_state.banknifty_data:
        info = st.session_state.banknifty_data
        display_dashboard(symbol_choice, info, info['vix_data'])
    else:
        st.info("कृपया एक सिम्बल चुनें और 'Refresh Data' पर क्लिक करें।")

if __name__ == "__main__":
    main()
