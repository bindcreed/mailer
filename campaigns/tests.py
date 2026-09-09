import io

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
import openpyxl

from campaigns.models import SMTPConfig, SendingSession, Recipient
from campaigns.services import crypto, excel_import, sender


class CryptoTests(TestCase):
    def test_round_trip(self):
        token = crypto.encrypt("hunter2")
        self.assertNotEqual(token, "hunter2")
        self.assertEqual(crypto.decrypt(token), "hunter2")

    def test_empty_string(self):
        self.assertEqual(crypto.encrypt(""), "")
        self.assertEqual(crypto.decrypt(""), "")


class ExcelImportTests(TestCase):
    def _xlsx(self, headers, rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
        for row in rows:
            ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return SimpleUploadedFile("test.xlsx", buf.read())

    def test_auto_detect_common_headers(self):
        f = self._xlsx(["Full Name", "Email Address"], [["Alice", "alice@example.com"]])
        sheet = excel_import.parse_spreadsheet(f)
        self.assertTrue(sheet.confident)
        self.assertEqual(len(sheet.rows), 1)
        self.assertTrue(sheet.rows[0].valid)

    def test_low_confidence_headers_require_mapping(self):
        f = self._xlsx(["Candidate", "Contact"], [["Alice", "alice@example.com"]])
        sheet = excel_import.parse_spreadsheet(f)
        self.assertFalse(sheet.confident)

    def test_invalid_and_duplicate_rows_flagged(self):
        f = self._xlsx(
            ["Name", "Email"],
            [["Bad", "not-an-email"], ["A", "a@example.com"], ["A2", "a@example.com"]],
        )
        sheet = excel_import.parse_spreadsheet(f)
        valid = [r for r in sheet.rows if r.valid]
        invalid = [r for r in sheet.rows if not r.valid]
        self.assertEqual(len(valid), 1)
        self.assertEqual(len(invalid), 2)


class FormatAddressTests(TestCase):
    def test_wrapped_with_name(self):
        self.assertEqual(sender.format_address("Alice Fon", "alice@example.com"), "Alice Fon <alice@example.com>")

    def test_wrapped_without_name(self):
        self.assertEqual(sender.format_address("", "alice@example.com"), "<alice@example.com>")


class ModelTests(TestCase):
    def test_recipient_unique_per_session(self):
        user = User.objects.create_user("u", password="p")
        smtp = SMTPConfig.objects.create(name="s", host="h", from_email="a@b.com")
        session = SendingSession.objects.create(
            name="s1", smtp_config=smtp, subject="hi", body="body", created_by=user
        )
        Recipient.objects.create(session=session, email="a@example.com")
        with self.assertRaises(Exception):
            Recipient.objects.create(session=session, email="a@example.com")

    def test_session_counts(self):
        user = User.objects.create_user("u2", password="p")
        smtp = SMTPConfig.objects.create(name="s2", host="h", from_email="a@b.com")
        session = SendingSession.objects.create(
            name="s2", smtp_config=smtp, subject="hi", body="body", created_by=user
        )
        Recipient.objects.create(session=session, email="a@example.com", status=Recipient.STATUS_SENT)
        Recipient.objects.create(session=session, email="b@example.com", status=Recipient.STATUS_PENDING)
        counts = session.counts()
        self.assertEqual(counts["sent"], 1)
        self.assertEqual(counts["pending"], 1)
        self.assertEqual(counts["total"], 2)
