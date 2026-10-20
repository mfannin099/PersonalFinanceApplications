"""Credit Card Statement Dashboard page."""

import altair as alt
import pandas as pd
import streamlit as st

from analytics import (
    category_zscores,
    share_of_wallet,
    spend_by_period_and_category,
    spend_by_weekday,
)
from categorize import (
    CATEGORIES,
    categorize_transactions,
    load_overrides,
    save_override,
    uncategorized_merchants,
)
from cleaning import clean_statement

st.set_page_config(page_title="Credit Card Dashboard", page_icon="💳", layout="wide")
st.title("💳 Credit Card Statement Dashboard")

uploaded_file = st.file_uploader("Choose a CSV file - Credit Card Statement", type=["csv"])
st.info(
    "📄 **Expected CSV Columns:** Transaction Date, Description, Amount "
    "(Type and Category columns are used when present).",
    icon="ℹ️",
)

if uploaded_file is None:
    st.stop()

raw_df = pd.read_csv(uploaded_file)
try:
    df = clean_statement(raw_df)
except ValueError as exc:
    st.error(str(exc))
    st.stop()

df = categorize_transactions(df)

st.subheader("✅ File Uploaded Successfully!")

# --- Categorization review: let the user fix anything flagged Uncategorized ---
uncategorized = uncategorized_merchants(df)
if uncategorized:
    with st.expander(f"🏷️ Review {len(uncategorized)} uncategorized merchant(s)", expanded=True):
        st.caption("Assign a category and save it so it's remembered next time this merchant shows up.")
        overrides = load_overrides()
        for merchant in uncategorized:
            col1, col2 = st.columns([3, 2])
            with col1:
                st.write(merchant)
            with col2:
                chosen = st.selectbox(
                    "Category",
                    options=CATEGORIES,
                    key=f"cat_{merchant}",
                    label_visibility="collapsed",
                )
            if chosen != "Uncategorized" and overrides.get(merchant) != chosen:
                save_override(merchant, chosen)
                st.rerun()

# --- Sorting ---
sort_options = [col for col in ["Category", "Amount", "Transaction Date"] if col in df.columns]
sort_by = st.selectbox("📊 Sort data by:", sort_options)
sort_order = st.radio("Sort order:", ["Ascending", "Descending"], horizontal=True)
df = df.sort_values(by=sort_by, ascending=(sort_order == "Ascending"))

# --- Sidebar filters ---
descriptions = df["Description"].dropna().unique()
categories = df["Category"].dropna().unique()
min_date, max_date = df["Transaction Date"].min().date(), df["Transaction Date"].max().date()
min_amount, max_amount = float(df["Amount"].min()), float(df["Amount"].max())

with st.sidebar:
    st.header("Filters")
    selected_merchants = st.multiselect(
        "Select Merchant(s):",
        options=sorted(descriptions),
        default=descriptions,
    )
    selected_categories = st.multiselect(
        "Select Category(s):",
        options=sorted(categories),
        default=categories,
    )
    date_range = st.date_input(
        "Transaction Date Range:",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )
    amount_range = st.slider(
        "Transaction Amount Range ($):",
        min_value=min_amount,
        max_value=max_amount if max_amount > min_amount else min_amount + 1.0,
        value=(min_amount, max_amount),
    )

start_date, end_date = date_range if len(date_range) == 2 else (min_date, max_date)

filtered_df = df[
    df["Description"].isin(selected_merchants)
    & df["Category"].isin(selected_categories)
    & (df["Transaction Date"].dt.date >= start_date)
    & (df["Transaction Date"].dt.date <= end_date)
    & (df["Amount"] >= amount_range[0])
    & (df["Amount"] <= amount_range[1])
]

edited_df = st.data_editor(
    filtered_df,
    column_config={
        "Was Venmoed": st.column_config.CheckboxColumn(
            "Was Venmoed",
            help="Mark if this transaction was reimbursed via Venmo",
            default=False,
        ),
        "Split Count": st.column_config.NumberColumn(
            "Split Count",
            help="Number of people you split this purchase with",
            min_value=0,
            max_value=10,
            step=1,
            format="%d",
        ),
    },
    hide_index=True,
    use_container_width=True,
)

st.divider()

edited_df["Split Count"] = edited_df["Split Count"].replace(0, 1)
edited_df["Was Venmoed"] = edited_df["Was Venmoed"].apply(lambda x: str(x).strip().lower() == "true")
edited_df["Amount Venmoed"] = edited_df["Amount"] * (edited_df["Split Count"] - 1) / edited_df["Split Count"]
edited_df.loc[~edited_df["Was Venmoed"], "Amount Venmoed"] = 0

venmoed_df = edited_df[edited_df["Was Venmoed"]]
if not venmoed_df.empty:
    st.subheader("💸 Venmoed Transactions")
    st.dataframe(venmoed_df[["Category", "Amount", "Split Count", "Amount Venmoed"]], use_container_width=True)

st.divider()

# --- Metrics ---
col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("Number of Purchases", len(edited_df))
with col2:
    st.metric("Sum of Transactions", f"${edited_df['Amount'].sum():,.2f}")
with col3:
    st.metric("Number of Purchases Venmoed For", int(edited_df["Was Venmoed"].sum()))
with col4:
    st.metric("Sum of Venmo", f"${venmoed_df['Amount Venmoed'].sum():,.2f}" if not venmoed_df.empty else "$0.00")
with col5:
    st.metric("Average Purchase Amount", f"${edited_df['Amount'].mean():,.2f}")

st.header("Spending Overview")
top_n = st.slider("Number of values to show in bar charts:", min_value=3, max_value=25, value=10, step=1)
col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("### Spending by Category")
    category_spend = (
        edited_df.groupby("Category")["Amount"].sum().reset_index().sort_values("Amount", ascending=False).head(top_n)
    )
    chart1 = (
        alt.Chart(category_spend)
        .mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5)
        .encode(
            x=alt.X("Category:N", sort="-y", title="Category"),
            y=alt.Y("Amount:Q", title="Total Spent ($)"),
            tooltip=["Category", alt.Tooltip("Amount:Q", format="$.2f")],
        )
        .properties(height=400)
        .configure_axis(labelAngle=-30)
    )
    st.altair_chart(chart1, use_container_width=True)

    st.markdown("### Share of Wallet")
    wallet_df = share_of_wallet(edited_df)
    donut = (
        alt.Chart(wallet_df)
        .mark_arc(innerRadius=60)
        .encode(
            theta=alt.Theta("Amount:Q"),
            color=alt.Color("Category:N", legend=alt.Legend(title="Category")),
            tooltip=[
                "Category",
                alt.Tooltip("Amount:Q", format="$.2f"),
                alt.Tooltip("pct_of_total:Q", format=".1f", title="% of Total"),
            ],
        )
        .properties(height=300)
    )
    st.altair_chart(donut, use_container_width=True)

with col2:
    st.markdown("### Top Merchants by Spend")
    merchant_spend = (
        edited_df.groupby("Description")["Amount"].sum().reset_index().sort_values("Amount", ascending=False).head(top_n)
    )
    chart2 = (
        alt.Chart(merchant_spend)
        .mark_bar(cornerRadiusTopLeft=5, cornerRadiusBottomLeft=5)
        .encode(
            y=alt.Y("Description:N", sort="-x", title="Merchant / Place"),
            x=alt.X("Amount:Q", title="Total Spent ($)"),
            tooltip=["Description", alt.Tooltip("Amount:Q", format="$.2f")],
        )
        .properties(height=400)
    )
    st.altair_chart(chart2, use_container_width=True)

with col3:
    st.markdown("### Most Frequent Merchants")
    merchant_freq = (
        edited_df.groupby("Description")["Amount"]
        .count()
        .reset_index(name="Transaction Count")
        .sort_values("Transaction Count", ascending=False)
        .head(top_n)
    )
    chart3 = (
        alt.Chart(merchant_freq)
        .mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5, color="#6baed6")
        .encode(
            x=alt.X("Description:N", sort="-y", title="Merchant / Place"),
            y=alt.Y("Transaction Count:Q", title="Number of Transactions"),
            tooltip=["Description", "Transaction Count"],
        )
        .properties(height=400)
    )
    st.altair_chart(chart3, use_container_width=True)

st.markdown("### Spending by Day of Week")
weekday_spend = spend_by_weekday(edited_df)
weekday_chart = (
    alt.Chart(weekday_spend)
    .mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5, color="#6baed6")
    .encode(
        x=alt.X("Weekday:N", sort=weekday_spend["Weekday"].tolist(), title="Day of Week"),
        y=alt.Y("Amount:Q", title="Total Spent ($)"),
        tooltip=["Weekday", alt.Tooltip("Amount:Q", format="$.2f")],
    )
    .properties(height=300)
)
st.altair_chart(weekday_chart, use_container_width=True)

st.subheader("Spending Over Time")
agg_option = st.radio("Aggregate spending by:", ("Daily", "Weekly", "Monthly"), horizontal=True)
freq = {"Daily": "D", "Weekly": "W", "Monthly": "ME"}[agg_option]

time_spend = edited_df.set_index("Transaction Date").resample(freq)["Amount"].sum().reset_index()

time_chart = (
    alt.Chart(time_spend)
    .mark_line(point=alt.OverlayMarkDef(filled=True, size=60))
    .encode(
        x=alt.X("Transaction Date:T", title=agg_option, axis=alt.Axis(format="%b %d, %Y")),
        y=alt.Y("Amount:Q", title="Total Spent ($)"),
        tooltip=[
            alt.Tooltip("Transaction Date:T", title=agg_option),
            alt.Tooltip("Amount:Q", format="$.2f", title="Total Spent"),
        ],
        color=alt.value("#0078D4"),
    )
    .properties(height=400)
    .interactive()
)
st.altair_chart(time_chart, use_container_width=True)

st.markdown("### Spending by Category Over Time")
category_time_chart_type = st.radio(
    "Chart type:", ["Stacked Area", "Lines"], horizontal=True, key="category_time_chart_type"
)
category_time_spend = spend_by_period_and_category(edited_df, freq=freq)

category_time_encoding = dict(
    x=alt.X("Transaction Date:T", title=agg_option, axis=alt.Axis(format="%b %d, %Y")),
    y=alt.Y("Amount:Q", title="Total Spent ($)"),
    color=alt.Color("Category:N", legend=alt.Legend(title="Category")),
    tooltip=[
        "Category",
        alt.Tooltip("Transaction Date:T", title=agg_option),
        alt.Tooltip("Amount:Q", format="$.2f", title="Total Spent"),
    ],
)

if category_time_chart_type == "Stacked Area":
    category_time_chart = alt.Chart(category_time_spend).mark_area().encode(**category_time_encoding)
else:
    category_time_chart = (
        alt.Chart(category_time_spend)
        .mark_line(point=alt.OverlayMarkDef(filled=True, size=40))
        .encode(**category_time_encoding)
    )

st.altair_chart(category_time_chart.properties(height=400).interactive(), use_container_width=True)

# --- Recurring merchants ---
st.header("🔍 Money Saving Opportunities")
analysis_df = edited_df.copy()
analysis_df["Month"] = analysis_df["Transaction Date"].dt.to_period("M")

st.subheader("📆 Recurring Merchants")
months_seen = st.number_input(
    "Minimum number of months to qualify as recurring", min_value=1, max_value=12, value=3, step=1
)
monthly_counts = analysis_df.groupby(["Description", "Month"])["Amount"].sum().reset_index()
recurrence = monthly_counts.groupby("Description").size().reset_index(name="months_seen")
recurring_merchants = recurrence[recurrence["months_seen"] >= months_seen]["Description"].tolist()
analysis_df["is_recurring_candidate"] = analysis_df["Description"].isin(recurring_merchants)

st.dataframe(
    analysis_df[analysis_df["is_recurring_candidate"]]
    .groupby("Description")
    .size()
    .reset_index(name="Transaction Count")
    .sort_values("Transaction Count", ascending=False),
    use_container_width=True,
)

st.subheader("💸 Frequent Small Purchases")
col1, col2 = st.columns(2)
with col1:
    min_count = st.number_input("Minimum transactions per merchant", min_value=2, max_value=20, value=5, step=1)
with col2:
    max_avg = st.number_input("Maximum average amount ($)", min_value=5, max_value=50, value=25, step=5)

merchant_stats = analysis_df.groupby("Description")["Amount"].agg(["count", "mean"]).reset_index()
freq_small = merchant_stats[(merchant_stats["count"] >= min_count) & (merchant_stats["mean"] < max_avg)]
analysis_df["is_freq_small"] = analysis_df["Description"].isin(freq_small["Description"])

st.dataframe(
    analysis_df[analysis_df["is_freq_small"]]
    .groupby("Description")
    .size()
    .reset_index(name="Transaction Count")
    .sort_values("Transaction Count", ascending=False),
    use_container_width=True,
)

st.subheader("💰 Large One-Off Purchases")
z_threshold = st.slider("Z-score threshold", min_value=1.0, max_value=5.0, value=3.0, step=0.5)
analysis_df = category_zscores(analysis_df, z_threshold=z_threshold)

st.dataframe(
    analysis_df[analysis_df["is_one_off_large"]]
    .groupby("Description")
    .size()
    .reset_index(name="Transaction Count")
    .sort_values("Transaction Count", ascending=False),
    use_container_width=True,
)
st.caption("📊 Z-score is computed relative to each transaction's own category mean/std, not the whole statement.")

st.subheader("🆕 New Merchants")
days_threshold = st.slider("Days since first seen", min_value=15, max_value=180, value=60, step=15)
first_seen = analysis_df.groupby("Description")["Transaction Date"].min().reset_index(name="first_seen")
analysis_df = analysis_df.merge(first_seen, on="Description", how="left")
statement_end = analysis_df["Transaction Date"].max()
analysis_df["days_since_first"] = (statement_end - analysis_df["first_seen"]).dt.days
analysis_df["is_new_merchant"] = analysis_df["days_since_first"] <= days_threshold

new_merchant_summary = (
    analysis_df[analysis_df["is_new_merchant"]]
    .groupby("Description")
    .size()
    .reset_index(name="Transaction Count")
    .sort_values("Transaction Count", ascending=False)
)
st.dataframe(new_merchant_summary, use_container_width=True)
st.caption(f"🗓️ Found {new_merchant_summary['Description'].nunique()} new merchant(s).")

# --- Recommendations ---
st.header("💡 Money-Saving Recommendations")
st.info("Merchants flagged across two or more of the signals above are prioritized.")

flags = ["is_recurring_candidate", "is_freq_small", "is_one_off_large", "is_new_merchant"]
analysis_df["cut_score"] = analysis_df[flags].sum(axis=1)
candidate_df = analysis_df[analysis_df["cut_score"] >= 2].sort_values("cut_score", ascending=False)

candidate_summary = (
    candidate_df.groupby("Description")["Amount"]
    .agg(["count", "sum"])
    .reset_index()
    .rename(columns={"count": "Transactions", "sum": "Total Amount"})
    .sort_values("Total Amount", ascending=False)
)
candidate_summary["Total Amount"] = candidate_summary["Total Amount"].apply(lambda x: f"${x:,.2f}")
st.dataframe(candidate_summary, use_container_width=True)
st.metric("💰 Estimated Total Potential Savings", f"${candidate_df['Amount'].sum():,.2f}")
