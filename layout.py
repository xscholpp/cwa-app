"""
layout.py
---------
Shared page-layout tweaks. Streamlit's wide layout still reserves large
default side margins; widen_content() trims them so pages use more of the
screen (more room for things like side-by-side speaker/topic controls).
"""

import streamlit as st


def widen_content():
    # !important is required here: Streamlit sets its own block-container
    # padding via an inline style recomputed on a full page reload, which
    # otherwise wins over a plain class selector and makes the margins
    # "reset" to wide until you navigate away and back.
    st.markdown(
        """
        <style>
            .block-container {
                padding-left: 2rem !important;
                padding-right: 2rem !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )
