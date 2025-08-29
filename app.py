import React, { useState, useEffect } from 'react';
import { createRoot } from 'react-dom/client';

// Tailwind CSS is assumed to be available in the environment.
// Using inline SVGs instead of react-icons to avoid dependency issues.

const CheckIcon = ({ className }) => (
  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className={className}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
  </svg>
);

const XMarkIcon = ({ className }) => (
  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className={className}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 9.75l4.5 4.5m0-4.5l-4.5 4.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
  </svg>
);

const ArrowUpIcon = ({ className }) => (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className={className}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 15.75l7.5-7.5 7.5 7.5" />
    </svg>
);

const ArrowDownIcon = ({ className }) => (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className={className}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
    </svg>
);

const ExchangeIcon = ({ className }) => (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className={className}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 21L3 16.5m0 0L7.5 12M3 16.5h18M16.5 3l4.5 4.5m0 0l-4.5 4.5M21 7.5H3" />
    </svg>
);

const InfoCircleIcon = ({ className }) => (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className={className}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M11.25 11.25l.041.02a.75.75 0 010 1.06L10.5 13.5m0 0l-1.5 1.5m1.5-1.5l-1.5-1.5M12 21a9 9 0 110-18 9 9 0 010 18z" />
    </svg>
);


const App = () => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [symbol, setSymbol] = useState('NIFTY');
  const [tradeLog, setTradeLog] = useState([]);
  const [lastLoggedSignal, setLastLoggedSignal] = useState({});

  // Helper function to fetch data from a public API
  const fetchData = async (symbol) => {
    // Note: The NSE API has CORS restrictions. This fetch will likely fail without a proxy.
    // In a real-world scenario, you would use a server-side proxy to bypass CORS.
    const api_url = `https://www.nseindia.com/api/option-chain-indices?symbol=${symbol}`;
    const headers = {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
      'Accept-Language': 'en-US,en;q=0.9',
    };

    try {
      const response = await fetch(api_url, { headers });
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const jsonData = await response.json();
      return jsonData;
    } catch (e) {
      console.error("Error fetching data:", e);
      throw new Error(`Failed to fetch data. Error: ${e.message}`);
    }
  };

  const computeData = (apiData) => {
    if (!apiData || !apiData.records || !apiData.records.data) {
      throw new Error("Invalid API data format.");
    }

    const expiryDates = apiData.records.expiryDates;
    const currentExpiry = expiryDates[0];
    
    let pe_total_oi = 0;
    let ce_total_oi = 0;
    let pe_near_oi = 0;
    let ce_near_oi = 0;
    const underlying_price = apiData.records.underlyingValue;
    
    apiData.records.data.forEach(item => {
      pe_total_oi += item?.PE?.openInterest || 0;
      ce_total_oi += item?.CE?.openInterest || 0;
      
      if (item?.expiryDate === currentExpiry) {
        pe_near_oi += item?.PE?.openInterest || 0;
        ce_near_oi += item?.CE?.openInterest || 0;
      }
    });

    const pcr_total = ce_total_oi !== 0 ? pe_total_oi / ce_total_oi : Infinity;
    const pcr_near = ce_near_oi !== 0 ? pe_near_oi / ce_near_oi : Infinity;

    return {
      underlying: underlying_price,
      pcr_total: pcr_total,
      pcr_near: pcr_near,
      trend: pcr_near >= 1 ? "BULLISH" : "BEARISH",
      last_update: new Date().toLocaleTimeString(),
    };
  };

  const determineSignal = (pcr_near, trend) => {
    const pcr = pcr_near;
    let signal = "SIDEWAYS";
    let suggestedOption = null;
    
    // EMA signal is determined by PCR value for a fully automated strategy
    const ema_signal = trend === "BULLISH" ? "BUY" : "SELL";

    if (trend === "BULLISH" && ema_signal === "BUY" && pcr >= 1) {
      signal = "BUY";
      suggestedOption = "CALL";
    } else if (trend === "BEARISH" && ema_signal === "SELL" && pcr <= 1) {
      signal = "SELL";
      suggestedOption = "PUT";
    } else {
      signal = "SIDEWAYS";
      suggestedOption = null;
    }
    return { signal, suggestedOption };
  };

  const updateTradeLog = (currentData, signal, suggestedOption) => {
    // Auto-log a new trade if signal changes from SIDEYWAYS
    if (signal !== "SIDEWAYS") {
      const logKey = `${symbol}_${signal}`;
      const hasActiveTrade = tradeLog.some(log => log.status === 'Active' && log.symbol === symbol);
      
      if (!hasActiveTrade) {
        const newTrade = {
          timestamp: new Date().toLocaleString(),
          symbol,
          signal,
          suggestedOption: `${roundToNearestHundred(currentData.underlying)} ${suggestedOption}`,
          entryPrice: currentData.underlying,
          exitTime: null,
          currentPrice: currentData.underlying,
          pnl: 0,
          finalPnl: null,
          status: "Active",
        };
        setTradeLog(prevLog => [...prevLog, newTrade]);
        console.log(`New trade logged: ${signal} on ${symbol} at ${currentData.underlying}`);
      }
    }

    // Auto-exit active trades if signal changes or becomes SIDEYWAYS
    setTradeLog(prevLog => {
      return prevLog.map(log => {
        if (log.status === "Active" && log.symbol === symbol) {
          const oppositeSignal = log.signal === 'BUY' ? 'SELL' : 'BUY';
          if (signal === "SIDEWAYS" || signal === oppositeSignal) {
            const finalPnl = log.signal === 'BUY'
              ? (currentData.underlying - log.entryPrice) * 1
              : (log.entryPrice - currentData.underlying) * 1;
            
            console.log(`Trade for ${symbol} closed. Final P&L: ₹${finalPnl.toFixed(2)}`);
            
            return {
              ...log,
              status: "Closed",
              exitTime: new Date().toLocaleString(),
              finalPnl: finalPnl.toFixed(2),
              pnl: finalPnl,
            };
          }
        }
        return log;
      });
    });

    // Update live P&L for active trades
    setTradeLog(prevLog => {
      return prevLog.map(log => {
        if (log.status === "Active" && log.symbol === symbol) {
          const pnl = log.signal === 'BUY'
            ? (currentData.underlying - log.entryPrice) * 1
            : (log.entryPrice - currentData.underlying) * 1;
          
          return {
            ...log,
            currentPrice: currentData.underlying,
            pnl: pnl.toFixed(2),
          };
        }
        return log;
      });
    });
  };

  const getVixLabel = (vixValue) => {
    if (vixValue === null) {
      return { value: 'N/A', label: "Not Available", advice: "Volatility data is not available." };
    }
    if (vixValue < 15) {
      return { value: vixValue.toFixed(2), label: "Low Volatility", advice: "Low volatility in the market. Big price swings are not expected." };
    } else if (vixValue >= 15 && vixValue <= 25) {
      return { value: vixValue.toFixed(2), label: "Medium Volatility", advice: "Market has medium volatility. You can trade as per your strategy." };
    } else {
      return { value: vixValue.toFixed(2), label: "High Volatility", advice: "Market has very high volatility. Trade with extreme caution or avoid trading." };
    }
  };
  
  const roundToNearestHundred = (price) => {
    return Math.round(price / 100) * 100;
  };
  

  useEffect(() => {
    const fetchAllData = async () => {
      setLoading(true);
      setError(null);
      try {
        const optionData = await fetchData(symbol);
        const processedData = computeData(optionData);
        
        // Fetch VIX data
        const vixApiUrl = 'https://www.nseindia.com/api/all-indices';
        const vixResponse = await fetch(vixApiUrl, {
            headers: { 'User-Agent': 'Mozilla/5.0' },
        });
        const vixDataJson = await vixResponse.json();
        const vixValue = vixDataJson.data.find(d => d.index === 'India VIX')?.lastPrice || null;

        setData({
          ...processedData,
          vix_data: getVixLabel(vixValue)
        });
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    };

    fetchAllData(); // Initial fetch

    const intervalId = setInterval(() => {
      fetchAllData();
    }, 2000); // Refresh every 2 seconds

    return () => clearInterval(intervalId);
  }, [symbol]);

  // Effect to handle auto-logging and exiting of trades
  useEffect(() => {
    if (data) {
      const { signal, suggestedOption } = determineSignal(data.pcr_near, data.trend);
      updateTradeLog(data, signal, suggestedOption);
    }
  }, [data]);

  return (
    <div className="bg-gray-900 min-h-screen text-white p-4 sm:p-8 font-inter">
      <div className="max-w-4xl mx-auto">
        <h1 className="text-3xl sm:text-4xl font-bold text-center mb-6 text-yellow-400">NSE Option Chain Dashboard</h1>
        <p className="text-center text-gray-400 mb-8">Live automated analysis of NIFTY and BANKNIFTY.</p>
        
        <div className="flex justify-center mb-8">
          <div className="flex space-x-2 bg-gray-800 p-2 rounded-lg">
            <button
              className={`px-4 py-2 rounded-md font-semibold ${symbol === 'NIFTY' ? 'bg-indigo-600 text-white' : 'bg-gray-700 text-gray-300'}`}
              onClick={() => setSymbol('NIFTY')}
            >
              NIFTY
            </button>
            <button
              className={`px-4 py-2 rounded-md font-semibold ${symbol === 'BANKNIFTY' ? 'bg-indigo-600 text-white' : 'bg-gray-700 text-gray-300'}`}
              onClick={() => setSymbol('BANKNIFTY')}
            >
              BANKNIFTY
            </button>
          </div>
        </div>

        {loading ? (
          <div className="text-center text-gray-400 mt-20">Loading live data...</div>
        ) : error ? (
          <div className="bg-red-900 text-red-300 p-4 rounded-lg mt-8 text-center">
            <InfoCircleIcon className="inline-block mr-2 h-5 w-5" />
            {error}. The app will retry automatically.
          </div>
        ) : (
          <div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
              <div className="bg-gray-800 rounded-lg p-4 text-center shadow-lg border-t-4 border-indigo-600">
                <p className="text-gray-400 text-sm mb-1">Live Price</p>
                <p className="text-xl sm:text-2xl font-bold text-green-400">₹ {data.underlying.toFixed(2)}</p>
              </div>
              <div className="bg-gray-800 rounded-lg p-4 text-center shadow-lg border-t-4 border-yellow-400">
                <p className="text-gray-400 text-sm mb-1">Total PCR</p>
                <p className="text-xl sm:text-2xl font-bold">{data.pcr_total.toFixed(2)}</p>
              </div>
              <div className="bg-gray-800 rounded-lg p-4 text-center shadow-lg border-t-4 border-red-500">
                <p className="text-gray-400 text-sm mb-1">Near PCR</p>
                <p className="text-xl sm:text-2xl font-bold">{data.pcr_near.toFixed(2)}</p>
              </div>
              <div className="bg-gray-800 rounded-lg p-4 text-center shadow-lg border-t-4 border-cyan-400">
                <p className="text-gray-400 text-sm mb-1">India VIX</p>
                <p className="text-xl sm:text-2xl font-bold">{data.vix_data.value}</p>
                <p className="text-xs text-gray-400">{data.vix_data.label}</p>
              </div>
            </div>
            
            <div className="bg-gray-800 rounded-lg p-6 shadow-lg mb-8">
                <h3 className="text-2xl font-bold mb-4">Volatility Advice</h3>
                <p className="text-gray-300 flex items-start">
                    <InfoCircleIcon className="mt-1 mr-2 h-5 w-5 text-indigo-400 flex-shrink-0" />
                    <span className="leading-snug">{data.vix_data.advice}</span>
                </p>
            </div>

            <div className="bg-gray-800 rounded-lg p-6 shadow-lg mb-8">
              <h3 className="text-2xl font-bold mb-4">Strategy Signal</h3>
              <p className="text-gray-400 text-sm mb-4">Based on PCR and implied trend.</p>
              {(() => {
                const { signal, suggestedOption } = determineSignal(data.pcr_near, data.trend);
                if (signal === "BUY") {
                  return (
                    <div className="flex items-center bg-green-900 text-green-300 p-4 rounded-lg">
                      <ArrowUpIcon className="mr-3 h-6 w-6" />
                      <span className="font-semibold text-lg">Signal: BUY {suggestedOption}</span>
                    </div>
                  );
                } else if (signal === "SELL") {
                  return (
                    <div className="flex items-center bg-red-900 text-red-300 p-4 rounded-lg">
                      <ArrowDownIcon className="mr-3 h-6 w-6" />
                      <span className="font-semibold text-lg">Signal: SELL {suggestedOption}</span>
                    </div>
                  );
                } else {
                  return (
                    <div className="flex items-center bg-yellow-900 text-yellow-300 p-4 rounded-lg">
                      <ExchangeIcon className="mr-3 h-6 w-6" />
                      <span className="font-semibold text-lg">Signal: SIDEWAYS</span>
                    </div>
                  );
                }
              })()}
            </div>
          </div>
        )}

        <div className="bg-gray-800 rounded-lg p-6 shadow-lg">
          <h3 className="text-2xl font-bold mb-4">Paper Trade Log</h3>
          {tradeLog.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-700">
                <thead className="bg-gray-700">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Timestamp</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Symbol</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Signal</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Entry Price</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Exit Time</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">P&L</th>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-400 uppercase">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-700">
                  {tradeLog.slice().reverse().map((trade, index) => (
                    <tr key={index} className="hover:bg-gray-700">
                      <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-300">{trade.timestamp}</td>
                      <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-300">{trade.symbol}</td>
                      <td className="px-4 py-2 whitespace-nowrap text-sm font-semibold text-center">{trade.signal === 'BUY' ? 'BUY CE' : 'SELL PE'}</td>
                      <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-300">₹{trade.entryPrice.toFixed(2)}</td>
                      <td className="px-4 py-2 whitespace-nowrap text-sm text-gray-300">{trade.exitTime || '-'}</td>
                      <td className={`px-4 py-2 whitespace-nowrap text-sm font-bold ${trade.status === 'Closed' && trade.finalPnl > 0 ? 'text-green-400' : trade.status === 'Closed' && trade.finalPnl < 0 ? 'text-red-400' : trade.pnl > 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {trade.status === 'Closed' ? `₹${trade.finalPnl}` : `₹${trade.pnl}`}
                      </td>
                      <td className="px-4 py-2 whitespace-nowrap text-sm text-center">
                        {trade.status === 'Active' ? <CheckIcon className="h-5 w-5 text-green-500 inline-block" /> : <XMarkIcon className="h-5 w-5 text-red-500 inline-block" />}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-gray-400 text-center py-8">Trade log is empty.</div>
          )}
        </div>

        <p className="text-center text-gray-500 text-sm mt-8">
          Disclaimer: This is for educational purposes only. Do not use for live trading.
        </p>
      </div>
    </div>
  );
};

// Create a root element and render the app. This is the fix for the reported error.
const rootDiv = document.createElement('div');
rootDiv.id = 'root';
document.body.appendChild(rootDiv);

const root = createRoot(rootDiv);
root.render(<App />);
