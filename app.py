"""Plant Safety Inspection Tracker.

Log safety violations/hazards found during plant safety inspections, store
them in Google Sheets (or Supabase), and generate the meeting PPT on demand.
"""

import io
from datetime import date, datetime

import pandas as pd
import streamlit as st

from backend import auth_status_note, get_storage, logout_button, require_login
from ppt_builder import build_ppt
from storage import RECORD_HEADERS, STATUSES
from word_builder import build_psi_letter

st.set_page_config(
    page_title="Plant Safety Inspection Tracker",
    page_icon="🦺",
    layout="wide",
)

# Responsive tweaks so the app works well on phones: stack columns, enlarge
# touch targets, keep the metric row compact, and reclaim screen padding.
MOBILE_CSS = """
<style>
@media (max-width: 640px) {
  /* Stack side-by-side columns vertically on narrow screens */
  div[data-testid="stHorizontalBlock"] {
    flex-direction: column;
    gap: 0.5rem;
  }
  div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"],
  div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
    width: 100% !important;
    flex: 1 1 100% !important;
    min-width: 100% !important;
  }
  /* …but keep the small metric tiles (Total/Pending/Completed) in one row */
  div[data-testid="stHorizontalBlock"]:has(div[data-testid="stMetric"]) {
    flex-direction: row;
  }
  div[data-testid="stHorizontalBlock"]:has(div[data-testid="stMetric"]) >
  div[data-testid="stColumn"] {
    width: auto !important;
    flex: 1 1 30% !important;
    min-width: 30% !important;
  }
  /* Full-width buttons with comfortable touch targets. The button's whole
     wrapper chain shrink-wraps, so widen every level down to the button. */
  div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]),
  div[data-testid="stElementContainer"]:has(div[data-testid="stDownloadButton"]),
  div[data-testid="stElementContainer"]:has(div[data-testid="stFormSubmitButton"]),
  div[data-testid="stElementContainer"]:has(div[data-testid="stButton"]) > div,
  div[data-testid="stElementContainer"]:has(div[data-testid="stDownloadButton"]) > div,
  div[data-testid="stElementContainer"]:has(div[data-testid="stFormSubmitButton"]) > div,
  div[data-testid="stButton"],
  div[data-testid="stDownloadButton"],
  div[data-testid="stFormSubmitButton"] {
    width: 100% !important;
  }
  div[data-testid="stButton"] button,
  div[data-testid="stDownloadButton"] button,
  div[data-testid="stFormSubmitButton"] button {
    width: 100%;
    min-height: 2.75rem;
    font-size: 1rem;
  }
  /* Reclaim horizontal padding so content gets the full phone width */
  div[data-testid="stMainBlockContainer"],
  section.main > div.block-container {
    padding-left: 0.9rem;
    padding-right: 0.9rem;
    padding-top: 2.2rem;
  }
  /* Slightly smaller headings on phones */
  h1 { font-size: 1.45rem !important; }
  h2 { font-size: 1.2rem !important; }
  h3 { font-size: 1.05rem !important; }
  /* Comfortable tab labels; the tab bar swipes horizontally */
  .stTabs [data-baseweb="tab"] {
    padding: 0.4rem 0.7rem;
    font-size: 0.95rem;
  }
}
/* Comfortable spacing for radio options on touch screens at any size */
div[data-testid="stRadio"] div[role="radiogroup"] { gap: 0.6rem; }
</style>
"""

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

# Departments responsible for compliance. Edit here if the list changes —
# the app also shows any other department names already present in saved
# records, so old data never disappears from filters.
DEPARTMENTS = [
    "Electrical",
    "Mechanical",
    "Civil",
    "Stores",
    "Electrical Maintenance department",
    "Mechanical Maintenance department",
    "Production control Organization electrical",
    "Production control Organization Mechanical",
    "Other",
]


def department_options(df=None):
    """DEPARTMENTS plus any department values already present in the data."""
    options = list(DEPARTMENTS)
    if df is not None and "Department" in df:
        for value in df["Department"].unique():
            value = str(value).strip()
            if value and value not in options:
                options.append(value)
    return options


# Image compression presets -> (max longest side in px, JPEG quality).
# Higher = better resolution but larger; lower = smaller size in the database.
COMPRESSION_PRESETS = {
    "Balanced — good quality, smaller size (recommended)": (1400, 80),
    "High resolution — larger files": (1800, 88),
    "Maximum compression — smallest size": (1000, 65),
}
COMPRESSION_HELP = (
    "Photos are resized and re-encoded before storing, to save database space "
    "while keeping good resolution. Pick how much to compress."
)


def compression_selector(key):
    """Render the compression preset picker and return (max_px, quality)."""
    label = st.selectbox(
        "Photo quality / compression",
        list(COMPRESSION_PRESETS),
        help=COMPRESSION_HELP,
        key=key,
    )
    return COMPRESSION_PRESETS[label]


def load_records(storage):
    return storage.fetch_records()


def records_dataframe(records):
    df = pd.DataFrame(records, columns=RECORD_HEADERS).fillna("")
    return df


def to_excel_bytes(df):
    """Serialise a records dataframe to .xlsx bytes."""
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Records")
    return out.getvalue()


def main():
    st.markdown(MOBILE_CSS, unsafe_allow_html=True)
    if not require_login("password", title="🦺 Plant Safety Inspection — Sign in"):
        return

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
        logout_button("password")
        auth_status_note()

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

    dept_safety, dept_other = st.tabs(["👷 Safety Officer", "🏭 Other Department"])

    with dept_safety:
        tab_new, tab_records, tab_ppt = st.tabs(
            ["➕ New Entry", "📋 Records", "🎞️ Generate PPT"]
        )
        with tab_new:
            render_new_entry(storage)
        with tab_records:
            render_records(storage)
        with tab_ppt:
            render_generate_ppt(storage)

    with dept_other:
        render_compliance(storage)


def render_new_entry(storage):
    st.subheader("Log a violation / hazard")
    with st.form("new_entry", clear_on_submit=True):
        col1, col2 = st.columns([2, 1])
        with col1:
            safety_officer = st.text_input(
                "Safety Officer (uploaded by)", placeholder="e.g. Er. R. Kumar, SSE/Safety"
            )
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
            department = st.selectbox(
                "Department (responsible)", department_options(),
                help="Which department has to act on this point.",
            )
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
        max_px, quality = compression_selector("new_entry_compression")
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
                    "safety_officer": safety_officer.strip(),
                    "department": department,
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
                max_px=max_px,
                quality=quality,
            )
        st.success(f"Saved record **{record_id}** ✅")


def render_records(storage):
    st.subheader("All records")
    records = load_records(storage)
    if not records:
        st.info("No records yet — add one in the **New Entry** tab.")
        return

    df = records_dataframe(records)
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        status_filter = st.multiselect("Status", STATUSES, default=STATUSES)
    with col2:
        dept_filter = st.selectbox(
            "Department", ["All departments"] + department_options(df)
        )
    with col3:
        search = st.text_input("Search", placeholder="Filter by any text…")

    dept_scope = df if dept_filter == "All departments" else df[df["Department"] == dept_filter]
    filtered = dept_scope[dept_scope["Status"].isin(status_filter)] if status_filter else dept_scope
    if search.strip():
        mask = filtered.apply(
            lambda row: row.astype(str).str.contains(search, case=False).any(), axis=1
        )
        filtered = filtered[mask]

    pending = (dept_scope["Status"] == "Pending").sum()
    completed = (dept_scope["Status"] == "Completed").sum()
    m1, m2, m3 = st.columns(3)
    m1.metric("Total", len(dept_scope))
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
    detailed = storage.get_photos_detailed(selected_id)

    detail_col, photo_col = st.columns([3, 2])
    with detail_col:
        st.markdown(
            f"**Safety Officer:** {record['Safety Officer'] or '—'} &nbsp;·&nbsp; "
            f"**Department:** {record['Department'] or '—'} &nbsp;·&nbsp; "
            f"**Status:** {record['Status']}\n\n"
            f"**Description:** {record['Description of Violation/Hazard']}\n\n"
            f"**First appeared:** {record['First Appeared On']} · "
            f"**Action:** {record['Action By']} · "
            f"**Category:** {CATEGORY_LABELS.get(record['Category'], record['Category'])}\n\n"
            f"**Inspector's remarks:** {record['Remarks'] or '—'}\n\n"
            f"**PDC:** {record['PDC'] or '—'} &nbsp;·&nbsp; "
            f"**Action remarks:** {record['Action Remarks'] or '—'}"
        )

        with st.expander("✏️ Edit point (correct the PSI)"):
            with st.form(f"edit_{selected_id}"):
                e_officer = st.text_input("Safety Officer", value=record["Safety Officer"])
                dept_opts = department_options(df)
                cur_dept = record["Department"] if record["Department"] in dept_opts else dept_opts[0]
                e_department = st.selectbox(
                    "Department (responsible)", dept_opts,
                    index=dept_opts.index(cur_dept),
                )
                e_location = st.text_input("Location / Shop", value=record["Location/Shop"])
                e_desc = st.text_area(
                    "Description of Violation / Hazard (this is what shows on the slide)",
                    value=record["Description of Violation/Hazard"],
                    height=110,
                )
                ec1, ec2 = st.columns(2)
                with ec1:
                    e_first = st.date_input(
                        "First appeared on", value=parse_date(record["First Appeared On"])
                    )
                    e_action = st.text_input("Action (responsible officer)", value=record["Action By"])
                with ec2:
                    cat_labels = list(CATEGORY_OPTIONS)
                    cur_label = CATEGORY_LABELS.get(record["Category"], cat_labels[0])
                    e_cat_label = st.selectbox(
                        "Category", cat_labels,
                        index=cat_labels.index(cur_label) if cur_label in cat_labels else 0,
                    )
                e_remarks = st.text_area("Inspector's remarks", value=record["Remarks"])
                save_edit = st.form_submit_button("💾 Save changes", type="primary")
            if save_edit:
                storage.update_fields(selected_id, {
                    "Safety Officer": e_officer.strip(),
                    "Department": e_department,
                    "Location/Shop": e_location.strip(),
                    "Description of Violation/Hazard": e_desc.strip(),
                    "First Appeared On": e_first.strftime("%d/%m/%Y"),
                    "Action By": e_action.strip(),
                    "Category": CATEGORY_OPTIONS[e_cat_label],
                    "Remarks": e_remarks.strip(),
                })
                st.success("Point updated.")
                st.rerun()

        new_status = st.radio(
            "Status",
            STATUSES,
            index=STATUSES.index(record["Status"]) if record["Status"] in STATUSES else 0,
            horizontal=True,
            key=f"status_{selected_id}",
        )
        b1, b2 = st.columns([1, 1])
        with b1:
            if st.button("✅ Update status", type="primary"):
                storage.update_record(selected_id, status=new_status)
                st.success("Status updated.")
                st.rerun()
        with b2:
            if st.button("🗑️ Delete record"):
                storage.delete_record(selected_id)
                st.success("Record deleted.")
                st.rerun()

        letter_photos = [p for _, p in detailed["before"]] or [p for _, p in detailed["after"]]
        st.download_button(
            "📄 Download PSI letter (Word)",
            data=build_psi_letter(record.to_dict(), letter_photos),
            file_name=f"PSI_{selected_id}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            help="Official PSI report letter for this point, in the standard format.",
        )
    with photo_col:
        for kind, title in (("before", "Before"), ("after", "After (rectified)")):
            if detailed[kind]:
                st.markdown(f"**{title}**")
                for photo_no, photo in detailed[kind]:
                    st.image(photo, use_container_width=True)
                    if st.button(
                        "🗑️ Remove this photo",
                        key=f"del_{selected_id}_{kind}_{photo_no}",
                    ):
                        storage.delete_photo(selected_id, kind, photo_no)
                        st.success("Photo removed.")
                        st.rerun()
        if not detailed["before"] and not detailed["after"]:
            st.caption("No photos attached.")

        st.divider()
        st.markdown("**Add / change photos**")
        add_kind = st.radio(
            "Add as",
            ["BEFORE (violation)", "AFTER (rectified)"],
            horizontal=True,
            key=f"kind_{selected_id}",
        )
        new_photos = st.file_uploader(
            "Photos to add (compressed automatically before saving)",
            type=["jpg", "jpeg", "png"],
            accept_multiple_files=True,
            key=f"addph_{selected_id}",
        )
        if new_photos and st.button("📤 Upload photos", key=f"upl_{selected_id}"):
            kind = "before" if add_kind.startswith("BEFORE") else "after"
            storage.add_photos(
                selected_id, [p.getvalue() for p in new_photos], kind=kind
            )
            st.success("Photos added.")
            st.rerun()


def parse_date(value):
    """Parse a stored dd/mm/YYYY string, falling back to today."""
    try:
        return datetime.strptime(str(value), "%d/%m/%Y").date()
    except (ValueError, TypeError):
        return date.today()


def render_compliance(storage):
    """Action-owner view: close out points by adding PDC, remarks and
    completion photos. The point itself is read-only here — it can't be
    edited or deleted from this tab."""
    st.subheader("Close out a point")
    st.caption(
        "Add the **PDC** (Probable Date of Completion), your **remarks** and "
        "**completion photos**. The point details themselves are read-only here."
    )
    records = load_records(storage)
    if not records:
        st.info("No points have been raised yet — add one in the **New Entry** tab.")
        return

    df = records_dataframe(records).fillna("")

    fcol1, fcol2 = st.columns([1, 2])
    with fcol1:
        dept_filter = st.selectbox(
            "Your department",
            ["All departments"] + department_options(df),
            key="compliance_dept",
        )
    with fcol2:
        scope = st.radio("Show", ["Pending", "All", "Completed"], horizontal=True)

    view = df if dept_filter == "All departments" else df[df["Department"] == dept_filter]
    if scope == "Pending":
        view = view[view["Status"] == "Pending"]
    elif scope == "Completed":
        view = view[view["Status"] == "Completed"]

    if view.empty:
        st.success("Nothing here 🎉")
        return

    labels = {
        f"{row['ID']} — {row['Description of Violation/Hazard'][:70]}": row["ID"]
        for _, row in view.iterrows()
    }
    picked = st.selectbox("Select a point", list(labels))
    record = df[df["ID"] == labels[picked]].iloc[0]
    record_id = record["ID"]

    detail_col, photo_col = st.columns([3, 2])
    with detail_col:
        st.markdown("#### 📌 Point raised (read-only)")
        st.markdown(
            f"**Safety Officer:** {record['Safety Officer'] or '—'} &nbsp;·&nbsp; "
            f"**Department:** {record['Department'] or '—'}\n\n"
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
    st.markdown("#### ✍️ Compliance")
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
            "Remarks (action taken)",
            value=record["Action Remarks"],
            placeholder="e.g. Bus bar sunken and earth pit provided on both sides.",
        )
        completion_photos = st.file_uploader(
            "Completion photos (rectified condition)",
            type=["jpg", "jpeg", "png"],
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


def render_generate_ppt(storage):
    st.subheader("Generate the meeting PPT")
    records = load_records(storage)
    if not records:
        st.info("No records yet — add one in the **New Entry** tab.")
        return

    df = records_dataframe(records)
    heading = st.text_input("Slide heading", value="PLANT SAFETY INSPECTION")
    gcol1, gcol2 = st.columns([1, 2])
    with gcol1:
        dept_filter = st.selectbox(
            "Department",
            ["All departments"] + department_options(df),
            key="ppt_dept",
        )
    with gcol2:
        scope = st.radio(
            "Which records?",
            ["All", "Pending only", "Completed only", "Pick specific records"],
            horizontal=True,
        )
    pool = df if dept_filter == "All departments" else df[df["Department"] == dept_filter]
    if scope == "Pending only":
        chosen = pool[pool["Status"] == "Pending"]
    elif scope == "Completed only":
        chosen = pool[pool["Status"] == "Completed"]
    elif scope == "Pick specific records":
        labels = {
            f"{row['ID']} — {row['Description of Violation/Hazard'][:60]}": row["ID"]
            for _, row in pool.iterrows()
        }
        picked = st.multiselect("Records", list(labels))
        chosen = pool[pool["ID"].isin([labels[p] for p in picked])]
    else:
        chosen = pool

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
