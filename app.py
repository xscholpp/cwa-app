"""
app.py
------
Home page and login/setup screen for the CWA Conference Manager.
Run with: python3 -m streamlit run app.py
"""

import streamlit as st
from database import initialize_database, get_connection
from auth import login, logout, get_current_user, create_user, PRESETS, PERMISSIONS
from layout import widen_content

initialize_database()

st.set_page_config(page_title="CWA Conference Manager", layout="wide")
widen_content()

# Check whether any user accounts exist yet
conn = get_connection()
user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
conn.close()

current_user = get_current_user()

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
elif current_user is None:
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

# ── Home page (logged in) ─────────────────────────────────────────────────────
else:
    col1, col2 = st.columns([5, 1])
    with col1:
        st.title("Conference on World Affairs")
        st.caption(f"Signed in as **{current_user['display_name']}**")
    with col2:
        st.write("")
        if st.button("Log out"):
            logout()
            st.rerun()

    st.divider()

    st.markdown("""
Use the **sidebar** to navigate:

| Section | Description |
|---|---|
| **Admin** | Conference days, committees, tracks, rooms, and user accounts |
| **Speakers** | Add and manage speakers, topics, and availability |
| **Panels** | Create and track conference panels |
| **Schedule** | Assign panels to rooms, dates, and times |
""")
