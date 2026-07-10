"""Shared storage-backend selection for both Streamlit apps.

Both app.py (inspector) and response_app.py (action owner) call get_storage()
so they always talk to the same backend / same data.
"""

import streamlit as st

from storage import GoogleSheetsStorage, LocalStorage, SupabaseStorage


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
