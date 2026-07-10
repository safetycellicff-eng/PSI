"""Safety Point Compliance — action-owner app.

A companion to app.py (the inspection tracker). The officers responsible for
closing points use this app: they can SEE every violation/hazard raised by the
inspection team, but they CANNOT edit or delete the point itself. They can only
add their compliance:

  * PDC  — Probable Date of Completion
  * their remarks (action taken), kept separate from the inspector's remarks
  * completion photos (stored as the record's "after" photos)
  * mark the point Completed

It reads and writes the SAME backend as app.py (Supabase / Google Sheets /
local demo), so both apps always share the same data.
"""

from datetime import date, datetime

import pandas as pd
import streamlit as st

from backend import get_storage
from storage import RECORD_HEADERS

st.set_page_config(
    page_title="Safety Point Compliance",
    page_icon="✅",
    layout="wide",
)

PHOTO_TYPES = ["jpg", "jpeg", "png"]


def parse_date(value):
    """Parse a stored dd/mm/YYYY string, falling back to today."""
    try:
        return datetime.strptime(str(value), "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return date.today()


def main():
    try:
        storage, backend_name = get_storage()
    except Exception as exc:
        st.error(
            "Could not connect to the configured storage backend. "
            "Check your `.streamlit/secrets.toml` (see README).\n\n"
            f"Details: {exc}"
        )
        st.stop()

    st.title("✅ Safety Point Compliance")
    st.caption(
        "Close out the points raised by the inspection team. You can add your "
        "**PDC**, **remarks** and **completion photos** — the points themselves "
        "are read-only here."
    )

    with st.sidebar:
        st.subheader("Storage")
        if backend_name == "Local (demo)":
            st.warning(
                "Running in **local demo mode** — this app and the tracker share "
                "the local `.local_data/` folder. Configure Supabase or Google "
                "Sheets in `.streamlit/secrets.toml` to share cloud data."
            )
        else:
            st.success(f"Connected to **{backend_name}**")
            if storage.url:
                st.markdown(f"[Open the Google Sheet ↗]({storage.url})")
        if st.button("🔄 Refresh data"):
            st.cache_data.clear()
            st.rerun()

    records = storage.fetch_records()
    if not records:
        st.info("No points have been raised yet.")
        return

    df = pd.DataFrame(records, columns=RECORD_HEADERS).fillna("")

    m1, m2, m3 = st.columns(3)
    m1.metric("Total points", len(df))
    m2.metric("Pending", int((df["Status"] == "Pending").sum()))
    m3.metric("Completed", int((df["Status"] == "Completed").sum()))

    scope = st.radio("Show", ["Pending", "All", "Completed"], horizontal=True)
    if scope == "Pending":
        view = df[df["Status"] == "Pending"]
    elif scope == "Completed":
        view = df[df["Status"] == "Completed"]
    else:
        view = df

    if view.empty:
        st.success("Nothing here 🎉")
        return

    labels = {
        f"{row['ID']} — {row['Description of Violation/Hazard'][:70]}": row["ID"]
        for _, row in view.iterrows()
    }
    picked = st.selectbox("Select a point", list(labels))
    record = df[df["ID"] == labels[picked]].iloc[0]
    render_point(storage, record)


def render_point(storage, record):
    record_id = record["ID"]

    detail_col, photo_col = st.columns([3, 2])
    with detail_col:
        st.markdown("### 📌 Point raised (read-only)")
        st.markdown(
            f"**Location / Shop:** {record['Location/Shop']}\n\n"
            f"**Description:** {record['Description of Violation/Hazard']}\n\n"
            f"**First appeared:** {record['First Appeared On']} &nbsp;·&nbsp; "
            f"**Action:** {record['Action By']} &nbsp;·&nbsp; "
            f"**Category:** {record['Category']} &nbsp;·&nbsp; "
            f"**Status:** {record['Status']}\n\n"
            f"**Inspector's remarks:** {record['Remarks'] or '—'}"
        )
    with photo_col:
        photos = storage.get_photos(record_id)
        if photos["before"]:
            st.markdown("**Point photos**")
            for photo in photos["before"]:
                st.image(photo, use_container_width=True)

    st.divider()
    st.markdown("### ✍️ Your compliance")
    with st.form(f"compliance_{record_id}"):
        col1, col2 = st.columns(2)
        with col1:
            pdc = st.date_input(
                "PDC — Probable Date of Completion",
                value=parse_date(record["PDC"]),
            )
        with col2:
            mark_done = st.checkbox(
                "Mark this point as Completed",
                value=(record["Status"] == "Completed"),
            )
        action_remarks = st.text_area(
            "Your remarks (action taken)",
            value=record["Action Remarks"],
            placeholder="e.g. Bus bar sunken and earth pit provided on both sides.",
        )
        completion_photos = st.file_uploader(
            "Completion photos (rectified condition)",
            type=PHOTO_TYPES,
            accept_multiple_files=True,
        )
        with st.expander("📷 Or capture a completion photo with the camera"):
            camera_photo = st.camera_input("Capture completion photo")
        submitted = st.form_submit_button("💾 Submit compliance", type="primary")

    if submitted:
        new_photos = [p.getvalue() for p in (completion_photos or [])]
        if camera_photo is not None:
            new_photos.append(camera_photo.getvalue())
        with st.spinner("Saving…"):
            storage.update_record(
                record_id,
                status="Completed" if mark_done else "Pending",
                pdc=pdc.strftime("%d/%m/%Y"),
                action_remarks=action_remarks.strip(),
            )
            if new_photos:
                storage.add_photos(record_id, new_photos[:4], kind="after")
        st.success("Compliance saved ✅")
        st.rerun()

    existing_after = storage.get_photos(record_id)["after"]
    if existing_after:
        st.markdown("**Completion photos already uploaded**")
        cols = st.columns(min(4, len(existing_after)))
        for i, photo in enumerate(existing_after):
            cols[i % len(cols)].image(photo, use_container_width=True)


if __name__ == "__main__":
    main()
