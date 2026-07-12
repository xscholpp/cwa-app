"""
layout.py
---------
Shared page-layout tweaks. Streamlit's wide layout still reserves large
default margins; widen_content() trims them so pages use more of the
screen (more room for things like side-by-side speaker/topic controls,
and so the page title sits close to the top instead of a big empty gap).
"""

import streamlit as st


def widen_content():
    # !important is required here: Streamlit sets its own block-container
    # padding via an inline style recomputed on a full page reload, which
    # otherwise wins over a plain class selector and makes the margins
    # "reset" to wide until you navigate away and back.
    #
    # padding-top is 4.5rem rather than 0 because Streamlit's fixed header
    # (hamburger/Deploy bar) is ~60px tall and would otherwise overlap the
    # page title.
    st.markdown(
        """
        <style>
            .block-container {
                padding-left: 2rem !important;
                padding-right: 2rem !important;
                padding-top: 4.5rem !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )
