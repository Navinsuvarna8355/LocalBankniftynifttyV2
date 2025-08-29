# app.py
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

# --- डेटा लाने के फंक्शन ---
def fetch_option_chain_from_api(symbol='BANKNIFTY'):
    """
    NSE API से लाइव ऑप्शन चेन डेटा लाता है।
    """
    api_url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    try:
        logging.info(f"Fetching data from third-party API for {symbol}...")
        response = requests.get(api_url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        logging.info("Data fetched successfully.")
        return data
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching data from API for {symbol}: {e}")
        raise Exception(f"Failed to fetch data. Error: {e}")

def fetch_vix_data():
    """
    NSE API से इंडिया VIX का मूल्य लाता है।
    """
    vix_api_url = "https://www.nseindia.com/api/all-indices"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    
    try:
        logging.info("Fetching India VIX data...")
        response = requests.get(vix_api_url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        for index in data.get('data', []):
            if index.get('index') == 'India VIX':
                return index.get('lastPrice')
        
        logging.warning("India VIX data not found in the response.")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching India VIX data: {e}")
        return None

def compute_oi_pcr_and_underlying(data):
    """
    लाए गए डेटा से PCR की गणना करता है और अंडरलाइंग प्राइस प्राप्त करता है।
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
        
        # पास की एक्सपायरी का डेटा जांचें
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

# --- रणनीति और UI फंक्शन ---
def determine_signal(pcr, trend, ema_signal):
    """
    PCR, ट्रेंड और EMA सिग्नल के आधार पर अंतिम ट्रेडिंग सिग्नल निर्धारित करता है।
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

def get_vix_label(vix_value):
    """
    VIX मूल्य के आधार पर एक अस्थिरता लेबल और सलाह देता है।
    """
    if vix_value is None:
        return {"value": 0, "label": "उपलब्ध नहीं", "advice": "अस्थिरता डेटा उपलब्ध नहीं है।"}
    if vix_value < 15:
        return {"value": vix_value, "label": "कम अस्थिरता", "advice": "बाजार में कम अस्थिरता है। बड़े मूल्य स्विंग्स की उम्मीद नहीं है।"}
    elif 15 <= vix_value <= 25:
        return {"value": vix_value, "label": "मध्यम अस्थिरता", "advice": "बाजार में मध्यम अस्थिरता है। आप अपनी रणनीति के अनुसार व्यापार कर सकते हैं।"}
    else:
        return {"value": vix_value, "label": "उच्च अस्थिरता", "advice": "बाजार में बहुत अधिक अस्थिरता है। बहुत सावधानी से व्यापार करें या व्यापार से बचें।"}

def display_dashboard(symbol, info, signal, suggested_side, vix_data):
    """
    ट्रेड लॉग और VIX सहित एक दिए गए प्रतीक के लिए डैशबोर्ड प्रदर्शित करता है।
    """
    # स्थानीय UI डिज़ाइन को दोहराने के लिए HTML का उपयोग करें
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

    # मुख्य कंटेनर
    st.markdown('<div class="main-container">', unsafe_allow_html=True)
    
    st.subheader(f"{symbol} ऑप्शन चेन डैशबोर्ड", help="PCR रणनीति के आधार पर लाइव विश्लेषण।")
    st.divider()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f'<div class="card blue-card">लाइव मूल्य<div style="font-size:1.5rem; font-weight: bold;">₹ {info["underlying"]:.2f}</div></div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="card">PCR<div style="font-size:1.5rem; font-weight: bold;">{info["pcr_total"]:.2f}</div></div>', unsafe_allow_html=True)
    with col3:
        st.markdown(f'<div class="card">ट्रेंड<div style="font-size:1.5rem; font-weight: bold;">{info["trend"]}</div></div>', unsafe_allow_html=True)
    with col4:
        st.markdown(f'<div class="card">इंडिया VIX<div style="font-size:1.5rem; font-weight: bold;">{vix_data["value"]:.2f}</div><div style="font-size:0.8rem;">{vix_data["label"]}</div></div>', unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("बाजार अस्थिरता सलाह")
    st.info(vix_data["advice"])
    st.markdown("---")

    st.subheader("रणनीति सिग्नल")
    
    # CE/PE पर स्पष्ट खरीद/बिक्री कार्रवाई दिखाएं
    if signal == "BUY":
        st.success(f"सिग्नल: खरीदें - CE - एट-द-मनी ऑप्शन सुझाया गया: ₹{round(info['underlying']/100)*100} CE")
    elif signal == "SELL":
        st.error(f"सिग्नल: बेचें - PE - एट-द-मनी ऑप्शन सुझाया गया: ₹{round(info['underlying']/100)*100} PE")
    else:
        st.info("सिग्नल: साइडवेज - कोई मजबूत सिग्नल नहीं मिला।")
        
    st.divider()
    
    st.write(f"डेटा स्रोत: NSE India | अंतिम अपडेट: {info['last_update']}")
    st.warning("अस्वीकरण: यह केवल शैक्षिक उद्देश्यों के लिए है। लाइव ट्रेडिंग के लिए उपयोग न करें।")

    st.markdown('</div>', unsafe_allow_html=True)

def main():
    """
    स्ट्रीमलीट ऐप चलाने का मुख्य फंक्शन।
    """
    st.set_page_config(
        page_title="NSE ऑप्शन चेन रणनीति",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    
    st.title("NSE ऑप्शन चेन विश्लेषण डैशबोर्ड")
    st.markdown("यह डैशबोर्ड एक कस्टम ट्रेडिंग रणनीति के आधार पर NIFTY और BANKNIFTY का लाइव विश्लेषण प्रदान करता है।")

    # व्यापार लॉग और डेटा के लिए सत्र स्थिति शुरू करें
    if 'trade_log' not in st.session_state:
        st.session_state.trade_log = []
    if 'nifty_data' not in st.session_state:
        st.session_state.nifty_data = None
    if 'banknifty_data' not in st.session_state:
        st.session_state.banknifty_data = None
    if 'last_logged_signal' not in st.session_state:
        st.session_state.last_logged_signal = {}
    
    # --- मुख्य पृष्ठ पर प्रतीक चयन के लिए UI ---
    symbol_choice = st.radio(
        "प्रतीक चुनें",
        ["NIFTY", "BANKNIFTY"],
        index=0,
        horizontal=True
    )

    # हर 2 सेकंड में डेटा स्वचालित रूप से रिफ्रेश करें
    st.markdown(f'<div style="text-align:center;">ऑटो-रिफ्रेश हो रहा है...</div>', unsafe_allow_html=True)
    time.sleep(2)
    st.rerun()

    # --- डेटा लाने और प्रदर्शित करने का लॉजिक ---
    
    # यदि डेटा अभी तक उपलब्ध नहीं है तो डेटा लाएं
    try:
        data = fetch_option_chain_from_api(symbol_choice)
        info = compute_oi_pcr_and_underlying(data)
        vix_value = fetch_vix_data()
        
        vix_data = get_vix_label(vix_value)
        
        # EMA सिग्नल को PCR के आधार पर निर्धारित करें
        use_near_pcr = True # पास की एक्सपायरी PCR का उपयोग करने के लिए हार्डकोड करें
        lot_size = 1 # लॉट साइज़ को 1 पर हार्डकोड करें
        
        pcr_used = info['pcr_near'] if use_near_pcr and info['pcr_near'] is not None else info['pcr_total']
        
        if pcr_used is not None:
            if pcr_used >= 1:
                ema_signal_choice = "BUY"
                trend = "BULLISH"
            else:
                ema_signal_choice = "SELL"
                trend = "BEARISH"
        else:
            ema_signal_choice = "SIDEWAYS"
            trend = "SIDEWAYS"
        
        signal, suggested_side = determine_signal(pcr_used, trend, ema_signal_choice)
        
        current_data = {
            'underlying': info['underlying'],
            'pcr_total': info['pcr_total'],
            'pcr_near': info['pcr_near'],
            'last_update': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'use_near_pcr': use_near_pcr,
            'pcr_used': pcr_used,
            'trend': trend,
            'ema_signal': ema_signal_choice,
            'signal': signal,
            'suggested_side': suggested_side,
            'lot_size': lot_size,
            'vix_data': vix_data
        }

        if symbol_choice == 'NIFTY':
            st.session_state.nifty_data = current_data
        elif symbol_choice == 'BANKNIFTY':
            st.session_state.banknifty_data = current_data
        
    except Exception as e:
        st.error(f"{symbol_choice} के लिए डेटा लाने में त्रुटि: {e}")
        st.info("कृपया डेटा को फिर से लाने के लिए ऐप को रिफ्रेश करें।")

    # --- ऑटो-लॉग और P&L अपडेट लॉजिक ---
    current_info = None
    if symbol_choice == 'NIFTY' and st.session_state.nifty_data:
        current_info = st.session_state.nifty_data
    elif symbol_choice == 'BANKNIFTY' and st.session_state.banknifty_data:
        current_info = st.session_state.banknifty_data

    if current_info and current_info['signal'] != "SIDEWAYS":
        log_key = f"{symbol_choice}_{current_info['signal']}"
        if st.session_state.last_logged_signal.get(log_key) != current_info['last_update']:
            
            log_entry = {
                "Timestamp": current_info['last_update'],
                "Symbol": symbol_choice,
                "Signal": current_info['signal'],
                "Suggested Option": f"₹{round(current_info['underlying']/100)*100} {current_info['suggested_side']}",
                "Entry Price": current_info['underlying'],
                "Exit Time": "-",
                "Current Price": current_info['underlying'],
                "P&L": 0.0,
                "Final P&L": "-",
                "Used PCR": f"{current_info['pcr_used']:.2f}",
                "Lot Size": lot_size,
                "Status": "Active"
            }
            st.session_state.trade_log.append(log_entry)
            st.session_state.last_logged_signal[log_key] = current_info['last_update']

    current_nifty_price = st.session_state.nifty_data['underlying'] if st.session_state.nifty_data else None
    current_banknifty_price = st.session_state.banknifty_data['underlying'] if st.session_state.banknifty_data else None
    current_signal_for_exit = current_info['signal'] if current_info else None

    for entry in list(st.session_state.trade_log):
        if entry['Status'] == "Active" and entry['Symbol'] == symbol_choice:
            if (current_signal_for_exit == "SELL" and entry['Signal'] == "BUY") or \
               (current_signal_for_exit == "BUY" and entry['Signal'] == "SELL") or \
               (current_signal_for_exit == "SIDEWAYS"):
                
                current_price = None
                if entry['Symbol'] == 'NIFTY' and current_nifty_price:
                    current_price = current_nifty_price
                elif entry['Symbol'] == 'BANKNIFTY' and current_banknifty_price:
                    current_price = current_banknifty_price
                
                if current_price:
                    for original_entry in st.session_state.trade_log:
                        if original_entry['Timestamp'] == entry['Timestamp'] and original_entry['Symbol'] == entry['Symbol']:
                            original_entry['Status'] = "Closed"
                            original_entry['Exit Time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            original_entry['Current Price'] = current_price
                            pnl_calc = (current_price - original_entry['Entry Price']) * original_entry['Lot Size'] if original_entry['Signal'] == "BUY" else (original_entry['Entry Price'] - current_price) * original_entry['Lot Size']
                            original_entry['P&L'] = 0.0
                            original_entry['Final P&L'] = pnl_calc
                            st.success(f"ट्रेड बंद कर दिया गया है। अंतिम P&L: ₹{pnl_calc:.2f}")
                            break
    
    for entry in st.session_state.trade_log:
        if entry['Status'] == "Active":
            current_symbol = entry['Symbol']
            current_signal = entry['Signal']
            current_entry_price = entry['Entry Price']
            
            if current_symbol == 'NIFTY' and current_nifty_price:
                current_price = current_nifty_price
            elif current_symbol == 'BANKNIFTY' and current_banknifty_price:
                current_price = current_banknifty_price
            else:
                continue

            if current_signal == "BUY":
                pnl = (current_price - current_entry_price) * entry['Lot Size']
            else:
                pnl = (current_entry_price - current_price) * entry['Lot Size']

            entry['Current Price'] = current_price
            entry['P&L'] = pnl

    if symbol_choice == 'NIFTY' and st.session_state.nifty_data:
        info = st.session_state.nifty_data
        display_dashboard(symbol_choice, info, info['signal'], info['suggested_side'], info['vix_data'])
    elif symbol_choice == 'BANKNIFTY' and st.session_state.banknifty_data:
        info = st.session_state.banknifty_data
        display_dashboard(symbol_choice, info, info['signal'], info['suggested_side'], info['vix_data'])
    else:
        st.info("कृपया एक प्रतीक चुनें।")
    
    st.subheader("व्यापार लॉग")
    if st.session_state.trade_log:
        display_log = []
        for entry in st.session_state.trade_log:
            display_entry = entry.copy()
            display_entry['P&L (लाइव/अंतिम)'] = f"₹{display_entry['P&L']:.2f}" if display_entry['Status'] == 'Active' else f"₹{display_entry['Final P&L']:.2f}"
            display_log.append(display_entry)
        
        df_log = pd.DataFrame(display_log)
        
        df_log = df_log.drop(columns=['P&L', 'Final P&L'])
        
        st.dataframe(df_log.style.apply(lambda x: ['background: #d4edda' if '₹' in str(x['P&L (लाइव/अंतिम)']) and float(str(x['P&L (लाइव/अंतिम)']).replace('₹', '')) > 0 else 'background: #f8d7da' if '₹' in str(x['P&L (लाइव/अंतिम)']) and float(str(x['P&L (लाइव/अंतिम)']).replace('₹', '')) < 0 else '' for i in x], axis=1))
    else:
        st.info("व्यापार लॉग खाली है। ऊपर एक व्यापार लॉग करें।")
    
if __name__ == "__main__":
    main()

