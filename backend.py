"""Storage-backend selection and password login for the Streamlit app.

app.py calls get_storage() to connect to the configured backend and
require_login() to gate the app behind a password kept in st.secrets.
"""

import streamlit as st

from storage import GoogleSheetsStorage, LocalStorage, SupabaseStorage


def _secrets():
    try:
        return dict(st.secrets)
    except FileNotFoundError:
        return {}


def require_login(app_key="password", title="🔒 Sign in"):
    """Gate the app behind a password read from st.secrets["auth"].

    app_key picks which password to check (e.g. "inspector_password"); it
    falls back to a shared "password". If no auth password is configured, the
    app stays open (so local/demo use isn't locked out). Returns True when the
    visitor may proceed.
    """
    auth = dict(_secrets().get("auth", {}))
    expected = auth.get(app_key) or auth.get("password")
    if not expected:
        return True  # no password configured -> app is open

    flag = f"auth_ok_{app_key}"
    if st.session_state.get(flag):
        return True

    st.title(title)
    st.caption("This app is password protected. Please enter the password to continue.")
    with st.form(f"login_{app_key}"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        if password == expected:
            st.session_state[flag] = True
            st.rerun()
        else:
            st.error("Incorrect password. Please try again.")
    return False


def logout_button(app_key="password"):
    """Show a 'Log out' button in the sidebar when the visitor is signed in."""
    flag = f"auth_ok_{app_key}"
    if st.session_state.get(flag) and st.sidebar.button("🚪 Log out"):
        del st.session_state[flag]
        st.rerun()


@st.cache_resource
def get_storage():
    """Pick the storage backend from st.secrets: Sheets > Supabase > local demo."""
    try:
        secrets = dict(st.secrets)
    except FileNotFoundError:
        secrets = {}
    if "gcp_service_account" in secrets:
        spreadsheet_id = secrets.get("spreadsheet_id", "")
        if "/" in spreadsheet_id:  # full URL pasted; extract the key
            spreadsheet_id = spreadsheet_id.split("/d/")[1].split("/")[0]
        return (
            GoogleSheetsStorage(dict(secrets["gcp_service_account"]), spreadsheet_id),
            "Google Sheets",
        )
    if "supabase" in secrets:
        sb = secrets["supabase"]
        return SupabaseStorage(sb["url"], sb["key"]), "Supabase"
    return LocalStorage(), "Local (demo)"
