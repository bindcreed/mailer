"""
Parses an uploaded spreadsheet (.xlsx/.xlsm or .csv) into rows of
(name, email), auto-detecting which columns hold the name and the
email address. If it can't confidently tell, the caller should fall
back to asking the user to map columns manually (see NAME_HEADERS /
EMAIL_HEADERS below, and `detect_columns`).
"""
import csv
import io
import re
from dataclasses import dataclass, field

import openpyxl

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

NAME_HEADERS = {"name", "full name", "fullname", "candidate name", "candidate", "applicant name", "first name"}
EMAIL_HEADERS = {"email", "e-mail", "email address", "e-mail address", "contact email", "mail"}


@dataclass
class ParsedRow:
    row_number: int  # 1-based, matches spreadsheet row (including header)
    name: str
    email: str
    valid: bool
    reason: str = ""


@dataclass
class ParsedSheet:
    headers: list
    rows: list = field(default_factory=list)  # list[ParsedRow]
    name_column: str = None
    email_column: str = None
    confident: bool = False


def _normalize(header) -> str:
    return str(header or "").strip().lower()


def detect_columns(headers):
    """Given a list of raw header strings, guess which is the name column
    and which is the email column. Returns (name_header, email_header, confident)."""
    normalized = {h: _normalize(h) for h in headers}

    name_col = next((h for h, n in normalized.items() if n in NAME_HEADERS), None)
    email_col = next((h for h, n in normalized.items() if n in EMAIL_HEADERS), None)

    # Loosen slightly if nothing matched exactly: look for "name"/"email" as substrings.
    if not name_col:
        name_col = next((h for h, n in normalized.items() if "name" in n and "file" not in n), None)
    if not email_col:
        email_col = next((h for h, n in normalized.items() if "mail" in n), None)

    confident = bool(name_col and email_col)
    return name_col, email_col, confident


def _rows_from_xlsx(file_obj):
    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return [], []
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    data_rows = rows[1:]
    return headers, data_rows


def _rows_from_csv(file_obj):
    text = file_obj.read()
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    headers = [h.strip() for h in rows[0]]
    data_rows = rows[1:]
    return headers, data_rows


def parse_spreadsheet(django_file, name_column=None, email_column=None) -> ParsedSheet:
    """Parse an uploaded file. If name_column/email_column are given, use
    those explicitly (the manual-mapping path); otherwise auto-detect."""
    filename = (django_file.name or "").lower()
    if filename.endswith(".csv"):
        headers, data_rows = _rows_from_csv(django_file)
    else:
        headers, data_rows = _rows_from_xlsx(django_file)

    sheet = ParsedSheet(headers=headers)
    if not headers:
        return sheet

    if name_column is None or email_column is None:
        detected_name, detected_email, confident = detect_columns(headers)
        name_column = name_column or detected_name
        email_column = email_column or detected_email
        sheet.confident = confident
    else:
        sheet.confident = True

    sheet.name_column = name_column
    sheet.email_column = email_column

    if not name_column or not email_column:
        return sheet  # caller must prompt for manual mapping

    name_idx = headers.index(name_column)
    email_idx = headers.index(email_column)

    seen_emails = set()
    for i, raw_row in enumerate(data_rows, start=2):  # row 2 is first data row
        if raw_row is None or all(c in (None, "") for c in raw_row):
            continue
        name_val = str(raw_row[name_idx]).strip() if name_idx < len(raw_row) and raw_row[name_idx] is not None else ""
        email_val = str(raw_row[email_idx]).strip() if email_idx < len(raw_row) and raw_row[email_idx] is not None else ""
        email_val = email_val.lower()

        if not email_val:
            sheet.rows.append(ParsedRow(i, name_val, email_val, valid=False, reason="Missing email"))
            continue
        if not EMAIL_RE.match(email_val):
            sheet.rows.append(ParsedRow(i, name_val, email_val, valid=False, reason="Not a valid email address"))
            continue
        if email_val in seen_emails:
            sheet.rows.append(ParsedRow(i, name_val, email_val, valid=False, reason="Duplicate in this file"))
            continue

        seen_emails.add(email_val)
        sheet.rows.append(ParsedRow(i, name_val, email_val, valid=True))

    return sheet
