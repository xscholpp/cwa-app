"""
layout.py
---------
Shared page-layout tweaks. Streamlit's wide layout still reserves large
default side margins; widen_content() trims them so pages use more of the
screen (more room for things like side-by-side speaker/topic controls).
"""

import streamlit as st


def widen_content():
    st.markdown(
        """
        <style>
            .block-container {
                padding-left: 2rem;
                padding-right: 2rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )
