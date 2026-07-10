"""Plant Safety Inspection Tracker.

Log safety violations/hazards found during plant safety inspections, store
them in Google Sheets (or Supabase), and generate the meeting PPT on demand.
"""

import io
from datetime import date, datetime

import pandas as pd
import streamlit as st

from backend import get_storage
from ppt_builder import build_ppt
from storage import RECORD_HEADERS, STATUSES

st.set_page_config(
    page_title="Plant Safety Inspection Tracker",
    page_icon="🦺",
    layout="wide",
)

# Full label shown in the UI -> short code stored and printed on the PPT marker.
CATEGORY_OPTIONS = {
    "SV – Safety Violation": "SV",
    "UA – Unsafe Act": "UA",
    "UC – Unsafe Condition": "UC",
    "NM – Near Miss": "NM",
}
CATEGORY_HELP = "Pick the type of finding. The short code (SV/UA/UC/NM) is shown on the slide marker."
# Short code -> full descriptive label, for display.
CATEGORY_LABELS = {code: label for label, code in CATEGORY_OPTIONS.items()}


def load_records(storage):
    return storage.fetch_records()


def records_dataframe(records):
    df = pd.DataFrame(records, columns=RECORD_HEADERS)
    return df


def to_excel_bytes(df):
    """Serialise a records dataframe to .xlsx bytes."""
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Records")
    return out.getvalue()


def main():
    try:
        storage, backend_name = get_storage()
    except Exception as exc:
        st.error(
            "Could not connect to the configured storage backend. "
            "Check your `.streamlit/secrets.toml` (see README for setup steps).\n\n"
            f"Details: {exc}"
        )
        st.stop()

    st.title("🦺 Plant Safety Inspection Tracker")

    with st.sidebar:
        st.subheader("Storage")
        if backend_name == "Local (demo)":
            st.warning(
                "Running in **local demo mode** — data is stored on this machine "
                "only. Configure Google Sheets or Supabase in "
                "`.streamlit/secrets.toml` to store data in the cloud (see README)."
            )
        else:
            st.success(f"Connected to **{backend_name}**")
            if storage.url:
                st.markdown(f"[Open the Google Sheet ↗]({storage.url})")
        if st.button("🔄 Refresh data"):
            st.cache_data.clear()
            st.rerun()

        with st.expander("⚠️ Danger zone"):
            st.caption(
                "Permanently delete **all** records and photos "
                f"from {backend_name}. This cannot be undone."
            )
            confirm = st.checkbox("I understand this cannot be undone")
            if st.button("🗑️ Delete all history", disabled=not confirm):
                storage.clear_all()
                st.cache_data.clear()
                st.success("All history deleted.")
                st.rerun()

    tab_new, tab_records, tab_ppt = st.tabs(
        ["➕ New Entry", "📋 Records", "🎞️ Generate PPT"]
    )

    with tab_new:
        render_new_entry(storage)
    with tab_records:
        render_records(storage)
    with tab_ppt:
        render_generate_ppt(storage)


def render_new_entry(storage):
    st.subheader("Log a violation / hazard")
    with st.form("new_entry", clear_on_submit=True):
        col1, col2 = st.columns([2, 1])
        with col1:
            location = st.text_input(
                "Location / Shop", placeholder="e.g. Shop-87 Commissioning shed"
            )
            description = st.text_area(
                "Description of Violation / Hazard",
                placeholder=(
                    "e.g. Earth bus bar found in exposed condition, "
                    "all bus bar to be fixed in wall."
                ),
                height=110,
            )
            remarks = st.text_area(
                "Remarks",
                placeholder="e.g. Earth bus bar should be sunken or buried with earth pit…",
                height=90,
            )
        with col2:
            first_appeared = st.date_input("First appeared on", value=date.today())
            action_by = st.text_input("Action (responsible officer)", placeholder="e.g. Dy.CEE/M")
            category_label = st.selectbox(
                "Category", list(CATEGORY_OPTIONS), help=CATEGORY_HELP
            )
            category = CATEGORY_OPTIONS[category_label]
            status = st.radio("Status", STATUSES, horizontal=True)
        photo_col1, photo_col2 = st.columns(2)
        with photo_col1:
            before_photos = st.file_uploader(
                "Violation photos — BEFORE (up to 4)",
                type=["jpg", "jpeg", "png"],
                accept_multiple_files=True,
            )
        with photo_col2:
            after_photos = st.file_uploader(
                "Rectified photos — AFTER (optional, up to 4)",
                type=["jpg", "jpeg", "png"],
                accept_multiple_files=True,
                help="If both BEFORE and AFTER photos are given, the slide shows "
                     "them side by side with red BEFORE/AFTER labels.",
            )
        with st.expander("📷 Or take a photo with the camera"):
            camera_photo = st.camera_input("Capture site photo (added as BEFORE)")
        submitted = st.form_submit_button("💾 Save record", type="primary")

    if submitted:
        if not description.strip():
            st.error("Description is required.")
            return
        before_list = [p.getvalue() for p in (before_photos or [])]
        after_list = [p.getvalue() for p in (after_photos or [])]
        if camera_photo is not None:
            before_list.append(camera_photo.getvalue())
        if len(before_list) > 4 or len(after_list) > 4:
            st.warning("Only the first 4 photos of each kind will be used on the slide.")
        full_description = (
            f"{location.strip()}, {description.strip()}" if location.strip() else description.strip()
        )
        with st.spinner("Saving…"):
            record_id = storage.add_record(
                {
                    "location": location.strip(),
                    "description": full_description,
                    "first_appeared": first_appeared.strftime("%d/%m/%Y"),
                    "action_by": action_by.strip(),
                    "remarks": remarks.strip(),
                    "category": category,
                    "status": status,
                },
                before_list[:4],
                after_list[:4],
            )
        st.success(f"Saved record **{record_id}** ✅")


def render_records(storage):
    st.subheader("All records")
    records = load_records(storage)
    if not records:
        st.info("No records yet — add one in the **New Entry** tab.")
        return

    df = records_dataframe(records)
    col1, col2 = st.columns([1, 2])
    with col1:
        status_filter = st.multiselect("Status", STATUSES, default=STATUSES)
    with col2:
        search = st.text_input("Search", placeholder="Filter by any text…")

    filtered = df[df["Status"].isin(status_filter)] if status_filter else df
    if search.strip():
        mask = filtered.apply(
            lambda row: row.astype(str).str.contains(search, case=False).any(), axis=1
        )
        filtered = filtered[mask]

    pending = (df["Status"] == "Pending").sum()
    completed = (df["Status"] == "Completed").sum()
    m1, m2, m3 = st.columns(3)
    m1.metric("Total", len(df))
    m2.metric("Pending", int(pending))
    m3.metric("Completed", int(completed))

    st.dataframe(filtered, use_container_width=True, hide_index=True)

    dl1, dl2, dl3 = st.columns(3)
    with dl1:
        st.download_button(
            "⬇️ All records (Excel)",
            data=to_excel_bytes(df),
            file_name=f"safety_records_{datetime.now().strftime('%d.%m.%Y')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with dl2:
        st.download_button(
            "⬇️ Filtered view (Excel)",
            data=to_excel_bytes(filtered),
            file_name=f"safety_records_filtered_{datetime.now().strftime('%d.%m.%Y')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    with dl3:
        if st.button("🎞️ Prepare PPT of these records"):
            with st.spinner("Building slides…"):
                chosen = filtered.to_dict("records")
                photos_by_id = storage.get_photos_bulk([r["ID"] for r in chosen])
                st.session_state["records_ppt"] = build_ppt(chosen, photos_by_id)
        if st.session_state.get("records_ppt"):
            st.download_button(
                "⬇️ Download PPT",
                data=st.session_state["records_ppt"],
                file_name=f"Plant_safety_inspection_{datetime.now().strftime('%d.%m.%Y')}.pptx",
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )

    st.divider()
    st.subheader("Manage a record")
    record_ids = filtered["ID"].tolist()
    if not record_ids:
        return
    selected_id = st.selectbox("Record", record_ids)
    record = df[df["ID"] == selected_id].iloc[0]

    detail_col, photo_col = st.columns([3, 2])
    with detail_col:
        st.markdown(
            f"**Description:** {record['Description of Violation/Hazard']}\n\n"
            f"**First appeared:** {record['First Appeared On']} · "
            f"**Action:** {record['Action By']} · "
            f"**Category:** {CATEGORY_LABELS.get(record['Category'], record['Category'])}\n\n"
            f"**Remarks:** {record['Remarks']}"
        )
        new_status = st.radio(
            "Status",
            STATUSES,
            index=STATUSES.index(record["Status"]) if record["Status"] in STATUSES else 0,
            horizontal=True,
            key=f"status_{selected_id}",
        )
        new_remarks = st.text_area("Remarks", value=record["Remarks"], key=f"remarks_{selected_id}")
        b1, b2 = st.columns([1, 1])
        with b1:
            if st.button("✅ Update record", type="primary"):
                storage.update_record(selected_id, status=new_status, remarks=new_remarks)
                st.success("Record updated.")
                st.rerun()
        with b2:
            if st.button("🗑️ Delete record"):
                storage.delete_record(selected_id)
                st.success("Record deleted.")
                st.rerun()
    with photo_col:
        photo_set = storage.get_photos(selected_id)
        if photo_set["before"]:
            st.markdown("**Before**")
            for photo in photo_set["before"]:
                st.image(photo, use_container_width=True)
        if photo_set["after"]:
            st.markdown("**After (rectified)**")
            for photo in photo_set["after"]:
                st.image(photo, use_container_width=True)
        if not photo_set["before"] and not photo_set["after"]:
            st.caption("No photos attached.")

        st.divider()
        new_after = st.file_uploader(
            "Add AFTER (rectified) photos",
            type=["jpg", "jpeg", "png"],
            accept_multiple_files=True,
            key=f"after_{selected_id}",
        )
        if new_after and st.button("📤 Attach AFTER photos"):
            storage.add_photos(selected_id, [p.getvalue() for p in new_after], kind="after")
            st.success("Photos attached.")
            st.rerun()


def render_generate_ppt(storage):
    st.subheader("Generate the meeting PPT")
    records = load_records(storage)
    if not records:
        st.info("No records yet — add one in the **New Entry** tab.")
        return

    df = records_dataframe(records)
    heading = st.text_input("Slide heading", value="PLANT SAFETY INSPECTION")
    scope = st.radio(
        "Which records?",
        ["All", "Pending only", "Completed only", "Pick specific records"],
        horizontal=True,
    )
    if scope == "Pending only":
        chosen = df[df["Status"] == "Pending"]
    elif scope == "Completed only":
        chosen = df[df["Status"] == "Completed"]
    elif scope == "Pick specific records":
        labels = {
            f"{row['ID']} — {row['Description of Violation/Hazard'][:60]}": row["ID"]
            for _, row in df.iterrows()
        }
        picked = st.multiselect("Records", list(labels))
        chosen = df[df["ID"].isin([labels[p] for p in picked])]
    else:
        chosen = df

    st.caption(f"{len(chosen)} slide(s) will be generated — one per record.")
    if chosen.empty:
        return

    if st.button("🎞️ Generate PPT", type="primary"):
        with st.spinner("Building slides…"):
            chosen_records = chosen.to_dict("records")
            photos_by_id = storage.get_photos_bulk([r["ID"] for r in chosen_records])
            ppt_bytes = build_ppt(chosen_records, photos_by_id, heading=heading)
        filename = f"Plant_safety_inspection_{datetime.now().strftime('%d.%m.%Y')}.pptx"
        st.download_button(
            "⬇️ Download PPT",
            data=ppt_bytes,
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        st.success(f"Generated **{len(chosen_records)}** slide(s).")


if __name__ == "__main__":
    main()
