"""
app.py
------
Entry point for the CWA Conference Manager. Builds the sidebar navigation
based on whether someone is logged in, then hands off to the selected page.
Run with: python3 -m streamlit run app.py
"""

import streamlit as st
from database import initialize_database
from auth import get_current_user
from layout import widen_content

initialize_database()

st.set_page_config(page_title="CWA Conference Manager", layout="wide")
widen_content()

current_user = get_current_user()

# The "remember me" cookie can only be read back after one component
# round-trip, so right after a hard page reload current_user briefly reads
# as None even for someone who is actually logged in. If Login were the
# *only* registered page during that split second, a reload of any other
# page (e.g. /Speakers) would 404 in Streamlit's router before the cookie
# has a chance to resolve. So the content pages always stay registered;
# only the default landing page and the presence of the Login page itself
# depend on the (possibly still-resolving) auth state.
content_pages = [
    st.Page("pages/home.py", title="Home", default=current_user is not None),
    st.Page("pages/0_Admin.py", title="Admin"),
    st.Page("pages/1_Speakers.py", title="Speakers"),
    st.Page("pages/2_Panels.py", title="Panels"),
    st.Page("pages/3_Schedule.py", title="Schedule"),
]

if current_user is None:
    pages = [st.Page("pages/login.py", title="Login", default=True)] + content_pages
else:
    pages = content_pages

pg = st.navigation(pages)
pg.run()
