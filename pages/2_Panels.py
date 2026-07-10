import streamlit as st
from auth import require_login

require_login()

st.title("Panels")
st.info("Coming soon — create and manage panels here.")
