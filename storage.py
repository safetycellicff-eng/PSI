"""Storage backends for safety inspection records.

Primary backend is Google Sheets (via a service account). Records go to a
"Records" worksheet; photos are stored as base64 chunks in a "Photos"
worksheet so everything lives in one spreadsheet with no extra Drive setup.
Photos are tagged as "before" (the violation) or "after" (rectified state).

A SupabaseStorage backend (table + storage bucket) is available as an
alternative, and a LocalStorage backend (CSV + image files under
.local_data/) is the demo fallback when no credentials are configured.
"""

import base64
import csv
import io
import os
import uuid
from datetime import datetime

from PIL import Image, ImageOps

RECORD_HEADERS = [
    "ID",
    "Created On",
    "Safety Officer",  # name of the officer who raised/uploaded the point
    "Department",      # department responsible for the point
    "Location/Shop",
    "Description of Violation/Hazard",
    "First Appeared On",
    "Action By",
    "Suggestions",
    "Category",
    "Status",
    "PDC",             # Probable Date of Completion, set by the action owner
    "Action Remarks",  # the action owner's remarks (distinct from inspector's)
    "Photo Count",
]

PHOTO_HEADERS = ["Record ID", "Kind", "Photo No", "Chunk No", "Data"]

PHOTO_KINDS = ("before", "after")

# Google Sheets cells hold at most 50,000 characters.
CHUNK_SIZE = 45000

STATUSES = ["Pending", "Completed"]


# Default compression: a good balance of resolution and stored size.
DEFAULT_MAX_PX = 1400
DEFAULT_QUALITY = 80


def prepare_photo(file_bytes, max_px=DEFAULT_MAX_PX, quality=DEFAULT_QUALITY):
    """Normalise + compress an uploaded photo.

    Fixes EXIF rotation, downscales so the longest side is at most ``max_px``
    (never upscaling, so resolution is preserved for smaller images), and
    re-encodes as an optimised JPEG at ``quality`` (1-95). Higher ``max_px`` /
    ``quality`` keep more detail; lower values save more space.
    """
    img = Image.open(io.BytesIO(file_bytes))
    img = ImageOps.exif_transpose(img)
    img.thumbnail((max_px, max_px))  # only shrinks; smaller images are untouched
    if img.mode != "RGB":
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


def new_record_id():
    return datetime.now().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:6].upper()


def empty_photo_set():
    return {kind: [] for kind in PHOTO_KINDS}


def _record_updates(status=None, remarks=None, pdc=None, action_remarks=None):
    """Build a {RECORD_HEADERS key: value} dict for update_fields, skipping Nones."""
    updates = {
        "Status": status,
        "Suggestions": remarks,
        "PDC": pdc,
        "Action Remarks": action_remarks,
    }
    return {header: value for header, value in updates.items() if value is not None}


class GoogleSheetsStorage:
    """Stores records and photos in a Google Spreadsheet."""

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

    def __init__(self, service_account_info, spreadsheet_id):
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_info(
            service_account_info, scopes=self.SCOPES
        )
        client = gspread.authorize(creds)
        self.spreadsheet = client.open_by_key(spreadsheet_id)
        self.records_ws = self._ensure_worksheet("Records", RECORD_HEADERS)
        self.photos_ws = self._ensure_worksheet("Photos", PHOTO_HEADERS)

    def _ensure_worksheet(self, title, headers):
        import gspread

        try:
            ws = self.spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.spreadsheet.add_worksheet(title, rows=200, cols=len(headers))
        first_row = ws.row_values(1)
        if first_row != headers:
            ws.update(values=[headers], range_name="A1")
        return ws

    def add_record(self, record, before_photos, after_photos=None,
                   max_px=DEFAULT_MAX_PX, quality=DEFAULT_QUALITY):
        record_id = new_record_id()
        after_photos = after_photos or []
        row = [
            record_id,
            datetime.now().strftime("%d/%m/%Y %H:%M"),
            record.get("safety_officer", ""),
            record.get("department", ""),
            record.get("location", ""),
            record.get("description", ""),
            record.get("first_appeared", ""),
            record.get("action_by", ""),
            record.get("remarks", ""),
            record.get("category", "SV"),
            record.get("status", "Pending"),
            record.get("pdc", ""),
            record.get("action_remarks", ""),
            len(before_photos) + len(after_photos),
        ]
        self.records_ws.append_row(row, value_input_option="RAW")
        rows = self._photo_rows(record_id, "before", before_photos, 1, max_px, quality)
        rows += self._photo_rows(record_id, "after", after_photos, 1, max_px, quality)
        if rows:
            self.photos_ws.append_rows(rows, value_input_option="RAW")
        return record_id

    def add_photos(self, record_id, photos, kind="after",
                   max_px=DEFAULT_MAX_PX, quality=DEFAULT_QUALITY):
        if not photos:
            return
        numbers = {k: self._photo_numbers(record_id, k) for k in PHOTO_KINDS}
        start_no = max(numbers[kind], default=0) + 1
        rows = self._photo_rows(record_id, kind, photos, start_no, max_px, quality)
        self.photos_ws.append_rows(rows, value_input_option="RAW")
        total = sum(len(v) for v in numbers.values()) + len(photos)
        row_no = self._find_record_row(record_id)
        self.records_ws.update_cell(
            row_no, RECORD_HEADERS.index("Photo Count") + 1, total
        )

    def _photo_numbers(self, record_id, kind):
        """Distinct photo numbers stored for a record/kind (may have gaps)."""
        numbers = set()
        for row in self.photos_ws.get_all_values()[1:]:
            if len(row) >= 3 and row[0] == record_id and row[1] == kind:
                try:
                    numbers.add(int(row[2]))
                except ValueError:
                    pass
        return sorted(numbers)

    def delete_photo(self, record_id, kind, photo_no):
        """Remove one photo (all its chunks) and refresh the Photo Count."""
        rows = self.photos_ws.get_all_values()
        doomed = [
            i for i, row in enumerate(rows[1:], start=2)
            if len(row) >= 3 and row[0] == record_id and row[1] == kind
            and row[2] == str(photo_no)
        ]
        for i in reversed(doomed):
            self.photos_ws.delete_rows(i)
        total = sum(len(self._photo_numbers(record_id, k)) for k in PHOTO_KINDS)
        row_no = self._find_record_row(record_id)
        self.records_ws.update_cell(
            row_no, RECORD_HEADERS.index("Photo Count") + 1, total
        )

    @staticmethod
    def _photo_rows(record_id, kind, photos, start_no,
                    max_px=DEFAULT_MAX_PX, quality=DEFAULT_QUALITY):
        rows = []
        for offset, photo_bytes in enumerate(photos):
            data = base64.b64encode(
                prepare_photo(photo_bytes, max_px, quality)
            ).decode("ascii")
            for chunk_no, start in enumerate(range(0, len(data), CHUNK_SIZE), start=1):
                rows.append([
                    record_id, kind, start_no + offset, chunk_no,
                    data[start : start + CHUNK_SIZE],
                ])
        return rows

    def fetch_records(self):
        rows = self.records_ws.get_all_values()
        if len(rows) <= 1:
            return []
        records = []
        for row in rows[1:]:
            if not any(cell.strip() for cell in row):
                continue
            row = row + [""] * (len(RECORD_HEADERS) - len(row))
            records.append(dict(zip(RECORD_HEADERS, row)))
        return records

    def get_photos(self, record_id):
        return self.get_photos_bulk([record_id]).get(record_id, empty_photo_set())

    def get_photos_detailed(self, record_id):
        """Like get_photos, but returns {kind: [(photo_no, bytes), ...]}."""
        wanted = {record_id}
        rows = self.photos_ws.get_all_values()[1:]
        chunks = {}
        for row in rows:
            if len(row) < 5 or row[0] not in wanted:
                continue
            _, kind, photo_no, chunk_no, data = row[:5]
            if kind not in PHOTO_KINDS:
                continue
            chunks.setdefault(kind, {}).setdefault(int(photo_no), {})[int(chunk_no)] = data
        result = {kind: [] for kind in PHOTO_KINDS}
        for kind, photos in chunks.items():
            for photo_no in sorted(photos):
                data = "".join(photos[photo_no][i] for i in sorted(photos[photo_no]))
                result[kind].append((photo_no, base64.b64decode(data)))
        return result

    def get_photos_bulk(self, record_ids):
        wanted = set(record_ids)
        rows = self.photos_ws.get_all_values()[1:]
        chunks = {}
        for row in rows:
            if len(row) < 5 or row[0] not in wanted:
                continue
            record_id, kind, photo_no, chunk_no, data = row[:5]
            if kind not in PHOTO_KINDS:
                continue
            chunks.setdefault(record_id, {}).setdefault(kind, {}).setdefault(
                int(photo_no), {}
            )[int(chunk_no)] = data
        result = {}
        for record_id, kinds in chunks.items():
            photo_set = empty_photo_set()
            for kind, photos in kinds.items():
                for photo_no in sorted(photos):
                    data = "".join(photos[photo_no][i] for i in sorted(photos[photo_no]))
                    photo_set[kind].append(base64.b64decode(data))
            result[record_id] = photo_set
        return result

    def _find_record_row(self, record_id):
        id_column = self.records_ws.col_values(1)
        for i, value in enumerate(id_column, start=1):
            if value == record_id:
                return i
        raise KeyError(f"Record {record_id} not found")

    def update_fields(self, record_id, updates):
        """Update arbitrary columns. ``updates`` maps RECORD_HEADERS keys to values."""
        row_no = self._find_record_row(record_id)
        for header, value in updates.items():
            self.records_ws.update_cell(row_no, RECORD_HEADERS.index(header) + 1, value)

    def update_record(self, record_id, status=None, remarks=None, pdc=None,
                      action_remarks=None):
        updates = _record_updates(status, remarks, pdc, action_remarks)
        if updates:
            self.update_fields(record_id, updates)

    def delete_record(self, record_id):
        row_no = self._find_record_row(record_id)
        self.records_ws.delete_rows(row_no)
        rows = self.photos_ws.get_all_values()
        doomed = [i for i, row in enumerate(rows[1:], start=2) if row and row[0] == record_id]
        for i in reversed(doomed):
            self.photos_ws.delete_rows(i)

    def clear_all(self):
        """Delete every record and photo, keeping the header rows."""
        n_records = len(self.records_ws.get_all_values())
        if n_records > 1:
            self.records_ws.delete_rows(2, n_records)
        n_photos = len(self.photos_ws.get_all_values())
        if n_photos > 1:
            self.photos_ws.delete_rows(2, n_photos)

    @property
    def url(self):
        return self.spreadsheet.url


class SupabaseStorage:
    """Stores records in a Supabase table and photos in a Supabase storage bucket.

    Expects a table named ``records`` (see README for the SQL) and a storage
    bucket named ``photos``.
    """

    TABLE = "records"
    BUCKET = "photos"

    COLUMN_MAP = {
        "ID": "id",
        "Created On": "created_on",
        "Safety Officer": "safety_officer",
        "Department": "department",
        "Location/Shop": "location",
        "Description of Violation/Hazard": "description",
        "First Appeared On": "first_appeared",
        "Action By": "action_by",
        "Suggestions": "remarks",
        "Category": "category",
        "Status": "status",
        "PDC": "pdc",
        "Action Remarks": "action_remarks",
        "Photo Count": "photo_count",
    }

    def __init__(self, url, key):
        from supabase import create_client

        self.client = create_client(url, key)

    def add_record(self, record, before_photos, after_photos=None,
                   max_px=DEFAULT_MAX_PX, quality=DEFAULT_QUALITY):
        record_id = new_record_id()
        after_photos = after_photos or []
        self.client.table(self.TABLE).insert(
            {
                "id": record_id,
                "created_on": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "safety_officer": record.get("safety_officer", ""),
                "department": record.get("department", ""),
                "location": record.get("location", ""),
                "description": record.get("description", ""),
                "first_appeared": record.get("first_appeared", ""),
                "action_by": record.get("action_by", ""),
                "remarks": record.get("remarks", ""),
                "category": record.get("category", "SV"),
                "status": record.get("status", "Pending"),
                "pdc": record.get("pdc", ""),
                "action_remarks": record.get("action_remarks", ""),
                "photo_count": len(before_photos) + len(after_photos),
            }
        ).execute()
        self._upload(record_id, "before", before_photos, 1, max_px, quality)
        self._upload(record_id, "after", after_photos, 1, max_px, quality)
        return record_id

    def _upload(self, record_id, kind, photos, start_no,
                max_px=DEFAULT_MAX_PX, quality=DEFAULT_QUALITY):
        storage = self.client.storage.from_(self.BUCKET)
        for offset, photo_bytes in enumerate(photos):
            storage.upload(
                f"{record_id}_{kind}_{start_no + offset}.jpg",
                prepare_photo(photo_bytes, max_px, quality),
                {"content-type": "image/jpeg"},
            )

    def add_photos(self, record_id, photos, kind="after",
                   max_px=DEFAULT_MAX_PX, quality=DEFAULT_QUALITY):
        if not photos:
            return
        numbers = {k: self._photo_numbers(record_id, k) for k in PHOTO_KINDS}
        start_no = max(numbers[kind], default=0) + 1
        self._upload(record_id, kind, photos, start_no, max_px, quality)
        total = sum(len(v) for v in numbers.values()) + len(photos)
        self.client.table(self.TABLE).update({"photo_count": total}).eq(
            "id", record_id
        ).execute()

    def fetch_records(self):
        response = (
            self.client.table(self.TABLE).select("*").order("created_on").execute()
        )
        records = []
        for row in response.data:
            records.append(
                {header: str(row.get(col, "") or "") for header, col in self.COLUMN_MAP.items()}
            )
        return records

    def _photo_numbers(self, record_id, kind):
        """Photo numbers stored in the bucket for a record/kind (may have gaps)."""
        prefix = f"{record_id}_{kind}_"
        try:
            objects = self.client.storage.from_(self.BUCKET).list(
                "", {"limit": 1000, "search": prefix}
            )
        except Exception:
            return []
        numbers = []
        for obj in objects or []:
            name = obj.get("name", "")
            if name.startswith(prefix) and name.endswith(".jpg"):
                try:
                    numbers.append(int(name[len(prefix):-4]))
                except ValueError:
                    pass
        return sorted(numbers)

    def get_photos(self, record_id):
        return {
            kind: [photo for _, photo in items]
            for kind, items in self.get_photos_detailed(record_id).items()
        }

    def get_photos_detailed(self, record_id):
        """Like get_photos, but returns {kind: [(photo_no, bytes), ...]}."""
        storage = self.client.storage.from_(self.BUCKET)
        result = {kind: [] for kind in PHOTO_KINDS}
        for kind in PHOTO_KINDS:
            for photo_no in self._photo_numbers(record_id, kind):
                try:
                    result[kind].append(
                        (photo_no, storage.download(f"{record_id}_{kind}_{photo_no}.jpg"))
                    )
                except Exception:
                    pass
        return result

    def delete_photo(self, record_id, kind, photo_no):
        """Remove one photo object and refresh the photo count."""
        self.client.storage.from_(self.BUCKET).remove(
            [f"{record_id}_{kind}_{photo_no}.jpg"]
        )
        total = sum(len(self._photo_numbers(record_id, k)) for k in PHOTO_KINDS)
        self.client.table(self.TABLE).update({"photo_count": total}).eq(
            "id", record_id
        ).execute()

    def get_photos_bulk(self, record_ids):
        return {rid: self.get_photos(rid) for rid in record_ids}

    def update_fields(self, record_id, updates):
        """Update arbitrary columns. ``updates`` maps RECORD_HEADERS keys to values."""
        changes = {self.COLUMN_MAP[header]: value for header, value in updates.items()}
        if changes:
            self.client.table(self.TABLE).update(changes).eq("id", record_id).execute()

    def update_record(self, record_id, status=None, remarks=None, pdc=None,
                      action_remarks=None):
        updates = _record_updates(status, remarks, pdc, action_remarks)
        if updates:
            self.update_fields(record_id, updates)

    def delete_record(self, record_id):
        names = [
            f"{record_id}_{kind}_{n}.jpg"
            for kind in PHOTO_KINDS
            for n in self._photo_numbers(record_id, kind)
        ]
        self.client.table(self.TABLE).delete().eq("id", record_id).execute()
        if names:
            self.client.storage.from_(self.BUCKET).remove(names)

    def clear_all(self):
        """Delete every record row and every photo object."""
        objects = self.client.storage.from_(self.BUCKET).list()
        names = [obj["name"] for obj in objects if obj.get("name")]
        if names:
            self.client.storage.from_(self.BUCKET).remove(names)
        # Supabase requires a filter on delete; every real id is non-empty.
        self.client.table(self.TABLE).delete().neq("id", "").execute()

    @property
    def url(self):
        return None


class LocalStorage:
    """CSV + image files under a local directory. Demo fallback only."""

    def __init__(self, base_dir=".local_data"):
        self.base_dir = base_dir
        self.records_csv = os.path.join(base_dir, "records.csv")
        self.photos_dir = os.path.join(base_dir, "photos")
        os.makedirs(self.photos_dir, exist_ok=True)
        if not os.path.exists(self.records_csv):
            self._write_all([])

    def _write_all(self, records):
        with open(self.records_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=RECORD_HEADERS)
            writer.writeheader()
            writer.writerows(records)

    def _photo_path(self, record_id, kind, photo_no):
        return os.path.join(self.photos_dir, f"{record_id}_{kind}_{photo_no}.jpg")

    def add_record(self, record, before_photos, after_photos=None,
                   max_px=DEFAULT_MAX_PX, quality=DEFAULT_QUALITY):
        record_id = new_record_id()
        after_photos = after_photos or []
        records = self.fetch_records()
        records.append(
            {
                "ID": record_id,
                "Created On": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "Safety Officer": record.get("safety_officer", ""),
                "Department": record.get("department", ""),
                "Location/Shop": record.get("location", ""),
                "Description of Violation/Hazard": record.get("description", ""),
                "First Appeared On": record.get("first_appeared", ""),
                "Action By": record.get("action_by", ""),
                "Suggestions": record.get("remarks", ""),
                "Category": record.get("category", "SV"),
                "Status": record.get("status", "Pending"),
                "PDC": record.get("pdc", ""),
                "Action Remarks": record.get("action_remarks", ""),
                "Photo Count": len(before_photos) + len(after_photos),
            }
        )
        self._write_all(records)
        for kind, photos in (("before", before_photos), ("after", after_photos)):
            for photo_no, photo_bytes in enumerate(photos, start=1):
                with open(self._photo_path(record_id, kind, photo_no), "wb") as f:
                    f.write(prepare_photo(photo_bytes, max_px, quality))
        return record_id

    def add_photos(self, record_id, photos, kind="after",
                   max_px=DEFAULT_MAX_PX, quality=DEFAULT_QUALITY):
        if not photos:
            return
        numbers = {k: self._photo_numbers(record_id, k) for k in PHOTO_KINDS}
        start_no = max(numbers[kind], default=0) + 1
        for offset, photo_bytes in enumerate(photos):
            with open(self._photo_path(record_id, kind, start_no + offset), "wb") as f:
                f.write(prepare_photo(photo_bytes, max_px, quality))
        total = sum(len(v) for v in numbers.values()) + len(photos)
        self.update_fields(record_id, {"Photo Count": total})

    def fetch_records(self):
        with open(self.records_csv, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def _photo_numbers(self, record_id, kind):
        """Photo numbers stored on disk for a record/kind (may have gaps)."""
        prefix = f"{record_id}_{kind}_"
        numbers = []
        for name in os.listdir(self.photos_dir):
            if name.startswith(prefix) and name.endswith(".jpg"):
                try:
                    numbers.append(int(name[len(prefix):-4]))
                except ValueError:
                    pass
        return sorted(numbers)

    def get_photos(self, record_id):
        return {
            kind: [photo for _, photo in items]
            for kind, items in self.get_photos_detailed(record_id).items()
        }

    def get_photos_detailed(self, record_id):
        """Like get_photos, but returns {kind: [(photo_no, bytes), ...]}."""
        result = {kind: [] for kind in PHOTO_KINDS}
        for kind in PHOTO_KINDS:
            for photo_no in self._photo_numbers(record_id, kind):
                with open(self._photo_path(record_id, kind, photo_no), "rb") as f:
                    result[kind].append((photo_no, f.read()))
        return result

    def delete_photo(self, record_id, kind, photo_no):
        """Remove one photo file and refresh the Photo Count."""
        path = self._photo_path(record_id, kind, photo_no)
        if os.path.exists(path):
            os.remove(path)
        total = sum(len(self._photo_numbers(record_id, k)) for k in PHOTO_KINDS)
        self.update_fields(record_id, {"Photo Count": total})

    def get_photos_bulk(self, record_ids):
        return {rid: self.get_photos(rid) for rid in record_ids}

    def update_fields(self, record_id, updates):
        """Update arbitrary columns. ``updates`` maps RECORD_HEADERS keys to values."""
        records = self.fetch_records()
        for record in records:
            if record["ID"] == record_id:
                record.update(updates)
        self._write_all(records)

    def update_record(self, record_id, status=None, remarks=None, pdc=None,
                      action_remarks=None):
        updates = _record_updates(status, remarks, pdc, action_remarks)
        if updates:
            self.update_fields(record_id, updates)

    def delete_record(self, record_id):
        records = [r for r in self.fetch_records() if r["ID"] != record_id]
        self._write_all(records)
        for kind in PHOTO_KINDS:
            for photo_no in self._photo_numbers(record_id, kind):
                os.remove(self._photo_path(record_id, kind, photo_no))

    def clear_all(self):
        """Delete every record and every stored photo file."""
        self._write_all([])
        for name in os.listdir(self.photos_dir):
            os.remove(os.path.join(self.photos_dir, name))

    @property
    def url(self):
        return None
