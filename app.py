import streamlit as st
import requests
import pandas as pd
import json

# --- Page Configuration ---
st.set_page_config(
    page_title="NSE Live Market Tracker",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.title("📈 NSE Live Market Tracker")
st.markdown("---")

# --- Function to fetch data from NSE using a robust method ---
@st.cache_data(ttl=60)  # Cache data for 60 seconds
def fetch_data(symbol):
    """
    Fetches live market data for a given symbol from NSE.
    This function uses a session and robust headers to mimic a browser
    request, which helps bypass security blocks.
    """
    st.info(f"Fetching data for {symbol}...")

    # Headers to mimic a browser request
    headers = {
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'referer': 'https://www.nseindia.com/option-chain',
        'accept-language': 'en-US,en;q=0.9',
        'accept-encoding': 'gzip, deflate, br',
        'host': 'www.nseindia.com'
    }

    try:
        # Step 1: Get cookies from the main NSE site to initiate a valid session
        session = requests.Session()
        session.get('https://www.nseindia.com/', headers=headers)
        
        # Step 2: Use the session and headers to fetch the actual data
        url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
        response = session.get(url, headers=headers)
        
        # Check for successful response
        response.raise_for_status()
        
        data = response.json()
        
        st.success("Data fetched successfully!")
        return data
    
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching data: {e}")
        st.error("NSE website might be blocking the request. Please try again after a few minutes.")
        return None
    except json.JSONDecodeError:
        st.error("Failed to decode JSON. The received data might be in an incorrect format.")
        return None


# --- Displaying Data ---
def display_data(symbol):
    data = fetch_data(symbol)
    if data:
        try:
            records = data.get('records', {})
            timestamp = records.get('timestamp')
            st.markdown(f"**Last Updated:** {timestamp}")
            st.markdown("---")

            # Extract CE and PE data
            calls = pd.DataFrame(records['CE'])
            puts = pd.DataFrame(records['PE'])

            # Add more descriptive columns for better readability
            # Call Options
            st.header(f"Call Options ({symbol})")
            ce_display = calls[[
                'strikePrice',
                'changeinOpenInterest',
                'pChangeinOpenInterest',
                'openInterest',
                'pchange',
                'lastPrice',
                'totalTradedVolume',
                'impliedVolatility'
            ]].rename(columns={
                'strikePrice': 'Strike Price',
                'changeinOpenInterest': 'Change in OI',
                'pChangeinOpenInterest': '% Change in OI',
                'openInterest': 'OI',
                'pchange': '% Change',
                'lastPrice': 'Last Price',
                'totalTradedVolume': 'Volume',
                'impliedVolatility': 'Implied Volatility (IV)'
            })
            st.dataframe(ce_display, use_container_width=True)

            # Put Options
            st.header(f"Put Options ({symbol})")
            pe_display = puts[[
                'strikePrice',
                'changeinOpenInterest',
                'pChangeinOpenInterest',
                'openInterest',
                'pchange',
                'lastPrice',
                'totalTradedVolume',
                'impliedVolatility'
            ]].rename(columns={
                'strikePrice': 'Strike Price',
                'changeinOpenInterest': 'Change in OI',
                'pChangeinOpenInterest': '% Change in OI',
                'openInterest': 'OI',
                'pchange': '% Change',
                'lastPrice': 'Last Price',
                'totalTradedVolume': 'Volume',
                'impliedVolatility': 'Implied Volatility (IV)'
            })
            st.dataframe(pe_display, use_container_width=True)

        except Exception as e:
            st.error(f"An error occurred while displaying data: {e}")
            st.info("The data structure might have changed. Please try refreshing the page.")

# --- Main App Logic ---
if __name__ == "__main__":
    st.sidebar.header("Options")
    selected_symbol = st.sidebar.selectbox("Select Symbol", ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIF"])
    
    st.subheader(f"Option Chain for {selected_symbol}")
    display_data(selected_symbol)
    
    # Refresh button
    if st.button("Refresh Data", key="refresh_button"):
        st.rerun()
