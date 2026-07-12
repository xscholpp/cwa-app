"""
pages/login.py
---------------
Account setup (first run) and sign-in screen. Only reachable while no one
is logged in — app.py excludes this page from navigation once you sign in.
"""

import streamlit as st
from database import get_connection
from auth import login, create_user, PERMISSIONS
from layout import widen_content

widen_content()

conn = get_connection()
user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
conn.close()

# ── First-time setup (no accounts exist yet) ──────────────────────────────────
if user_count == 0:
    st.title("Conference on World Affairs")
    st.subheader("Welcome — create your admin account to get started")
    st.info("No accounts have been set up yet. This first account will have full admin access.")

    with st.form("setup_form"):
        display_name = st.text_input("Your name")
        username     = st.text_input("Username")
        password     = st.text_input("Password", type="password")
        password2    = st.text_input("Confirm password", type="password")
        submitted    = st.form_submit_button("Create admin account")

    if submitted:
        if not all([display_name.strip(), username.strip(), password]):
            st.error("All fields are required.")
        elif password != password2:
            st.error("Passwords do not match.")
        else:
            admin_perms = {p: True for p in PERMISSIONS}
            ok, err = create_user(username, display_name, password, admin_perms)
            if ok:
                st.success("Admin account created. Please log in.")
                st.rerun()
            else:
                st.error(f"Could not create account: {err}")

# ── Login screen ──────────────────────────────────────────────────────────────
else:
    st.title("Conference on World Affairs")
    st.subheader("Sign in")

    with st.form("login_form"):
        username  = st.text_input("Username")
        password  = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted:
        if login(username, password):
            st.rerun()
        else:
            st.error("Invalid username or password.")
