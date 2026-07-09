# 🦺 Plant Safety Inspection Tracker

A small web app for logging safety violations/hazards found during plant
safety inspections, storing them in **Google Sheets** (or **Supabase**), and
generating the meeting **PowerPoint** on demand — one slide per record, in the
same format as the original safety-meeting deck (green title bar, details
table, site photos, category marker).

## What it does

- **➕ New Entry** — log a violation/hazard: location/shop, description, date
  it first appeared, responsible officer (e.g. `Dy.CEE/M`), remarks, category
  (SV/UA/UC/NM), status (Pending/Completed), and up to 4 site photos
  (upload files or capture with the camera).
- **📋 Records** — view, search and filter all records; update status/remarks
  when an item is completed; view photos; delete records; **export the whole
  list (or the filtered view) to Excel** (`.xlsx`), or **download those
  records as a PowerPoint** right from this tab.
- **🎞️ Generate PPT** — pick All / Pending / Completed / specific records and
  download a `.pptx` with one slide per record, matching the original format.
- **⚠️ Danger zone** (sidebar) — a **Delete all history** button that wipes
  every record and photo from the backend (guarded by a confirmation checkbox).

All data (including photos) is stored in your Google Sheet, so you can also
view and edit it directly in Google Sheets.

## Run it locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

With no credentials configured the app runs in **local demo mode** (data saved
to `.local_data/` on your machine) so you can try it immediately.

## Connect Google Sheets (recommended)

> **Is Google Cloud free for this?** Yes. The Google Sheets API and service
> accounts are free and need **no billing account or credit card** — only
> paid Google Cloud products (Compute Engine, BigQuery, etc.) require billing,
> and this app uses none of them. If you'd still rather avoid Google Cloud
> entirely, use the **Supabase** backend below instead.

1. **Create a Google Sheet** (e.g. "Plant Safety Inspections") at
   [sheets.new](https://sheets.new). Copy its URL.
2. **Create a service account:**
   1. Go to [Google Cloud Console](https://console.cloud.google.com/) and
      create (or pick) a project.
   2. Enable the **Google Sheets API**
      (APIs & Services → Library → search "Google Sheets API" → Enable).
   3. Go to APIs & Services → Credentials → **Create credentials →
      Service account**. Give it any name, click through the defaults.
   4. Open the service account → **Keys → Add key → Create new key → JSON**.
      A JSON file downloads.
3. **Share the sheet** with the service account: open your Google Sheet →
   Share → paste the service account's `client_email` (looks like
   `something@project.iam.gserviceaccount.com`) → give it **Editor** access.
4. **Configure secrets:** copy `.streamlit/secrets.toml.example` to
   `.streamlit/secrets.toml`, paste your sheet URL into `spreadsheet_id`, and
   copy the fields from the downloaded JSON into the `[gcp_service_account]`
   section.
5. Restart the app. The sidebar should show **Connected to Google Sheets**.

The app creates two worksheets automatically:

| Worksheet | Contents |
|-----------|----------|
| `Records` | One row per violation/hazard (ID, dates, description, action, remarks, category, status) |
| `Photos`  | Site photos stored as base64 chunks, linked to records by ID |

## Connect Supabase (alternative)

If you prefer Supabase instead of Google Sheets:

1. Create a project at [supabase.com](https://supabase.com).
2. In the SQL editor, run:

   ```sql
   create table records (
     id text primary key,
     created_on text,
     location text,
     description text,
     first_appeared text,
     action_by text,
     remarks text,
     category text,
     status text,
     photo_count int
   );
   ```

3. In Storage, create a **private bucket** named `photos`.
4. In `.streamlit/secrets.toml`, fill in the `[supabase]` section with your
   project URL and the **service_role** key (Settings → API), and remove the
   `[gcp_service_account]` section.

## Deploy for free (Streamlit Community Cloud)

1. Push this repository to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with
   GitHub, and pick this repo (`app.py` as the entry point).
3. In the app's **Settings → Secrets**, paste the contents of your
   `.streamlit/secrets.toml`.
4. Open the app URL from any device — including phones, for on-site
   photo capture.

## Files

| File | Purpose |
|------|---------|
| `app.py` | Streamlit UI (entry form, records view, PPT generation) |
| `storage.py` | Google Sheets / Supabase / local storage backends |
| `ppt_builder.py` | Builds the PPT in the original meeting format |
| `template.pptx` | Slide master/branding from the original deck |
