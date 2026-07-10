import streamlit as st
from auth import require_login, has_permission

require_login()

if not has_permission("can_manage_schedule"):
    st.error("You don't have permission to access this page.")
    st.stop()

st.title("Schedule")
st.info("Coming soon — build the conference schedule here.")
