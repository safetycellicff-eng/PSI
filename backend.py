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


# Used when no password is configured in secrets, so the app is NEVER open
# to the public. Change it by setting your own in secrets:
#   [auth]
#   password = "your-password"
DEFAULT_PASSWORD = "safety@123"


def _expected_password(app_key):
    """The configured password as a clean string (falls back to the default).

    Also accepts a top-level ``password`` secret (outside [auth]) so a
    slightly misplaced secret still protects the app. Numbers are accepted
    (TOML ``password = 1234`` without quotes becomes an int).
    """
    secrets = _secrets()
    auth = dict(secrets.get("auth", {}))
    value = auth.get(app_key) or auth.get("password") or secrets.get("password")
    if value is None or not str(value).strip():
        return DEFAULT_PASSWORD, True
    return str(value).strip(), False


def require_login(app_key="password", title="🔒 Sign in"):
    """Gate the app behind a password read from st.secrets["auth"].

    The app is always locked: if no password is configured, the built-in
    DEFAULT_PASSWORD applies. Returns True when the visitor may proceed.
    """
    expected, is_default = _expected_password(app_key)
    st.session_state["auth_mode"] = "default" if is_default else "custom"

    flag = f"auth_ok_{app_key}"
    if st.session_state.get(flag):
        return True

    st.title(title)
    st.caption("This app is password protected. Please enter the password to continue.")
    with st.form(f"login_{app_key}"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if submitted:
        if password.strip() == expected:
            st.session_state[flag] = True
            st.rerun()
        else:
            st.error("Incorrect password. Please try again.")
    return False


def auth_status_note():
    """Sidebar note (shown after login) when the default password is in use."""
    if st.session_state.get("auth_mode") == "default":
        st.warning(
            f"🔑 Using the **default password** (`{DEFAULT_PASSWORD}`). "
            "Set your own in the app secrets:\n\n"
            '```toml\n[auth]\npassword = "your-password"\n```',
            icon="⚠️",
        )


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
