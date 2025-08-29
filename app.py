# ----------------------------
# CE vs PE OI Live Chart
# ----------------------------
st.subheader("CE vs PE OI (Live Option Chain)")

symbol = st.selectbox("Symbol", ["NIFTY", "BANKNIFTY", "FINNIFTY"])
oc_data = get_option_chain(symbol, is_index_symbol(symbol))

if oc_data["strikes"]:
    import pandas as pd
    import altair as alt

    df = pd.DataFrame(oc_data["strikes"])
    # CE vs PE OI chart
    base = alt.Chart(df).encode(x=alt.X("strike:Q", title="Strike Price"))
    ce_line = base.mark_line(color="blue").encode(
        y=alt.Y("ce_oi:Q", title="Open Interest"),
        tooltip=["strike", "ce_oi"]
    )
    pe_line = base.mark_line(color="red").encode(
        y="pe_oi:Q",
        tooltip=["strike", "pe_oi"]
    )

    st.altair_chart(ce_line + pe_line, use_container_width=True)

    # Optional crossover flag
    df["crossover"] = df.apply(
        lambda r: "CE>PE" if r["ce_oi"] > r["pe_oi"] else "PE>CE", axis=1
    )
    st.dataframe(df[["strike", "ce_oi", "pe_oi", "crossover"]])
else:
    st.warning("⚠ Option chain data not available right now.")
