import streamlit as st

st.set_page_config(page_title="Personal Finance", page_icon="💰", layout="wide")

pages = [
    st.Page("CreditCard/page.py", title="Credit Card", icon="💳", default=True),
    st.Page("Stocks/page.py", title="Stocks", icon="📈"),
    st.Page("Budget/page.py", title="Budget", icon="📅"),
]

nav = st.navigation(pages)
nav.run()
