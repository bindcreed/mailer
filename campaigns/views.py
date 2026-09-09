import base64
import io
import smtplib

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.timezone import localtime

from campaigns.forms import SMTPConfigForm, SendingSessionForm, UploadForm
from campaigns.models import SMTPConfig, SendingSession, Recipient
from campaigns.services import excel_import, sender


# ---------- SMTP config ----------

@login_required
def smtp_list(request):
    return render(request, "campaigns/smtp_list.html", {"configs": SMTPConfig.objects.all()})


@login_required
def smtp_create(request):
    if request.method == "POST":
        form = SMTPConfigForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "SMTP server saved.")
            return redirect("smtp_list")
    else:
        form = SMTPConfigForm()
    return render(request, "campaigns/smtp_form.html", {"form": form, "is_new": True})


@login_required
def smtp_edit(request, pk):
    smtp = get_object_or_404(SMTPConfig, pk=pk)
    if request.method == "POST":
        form = SMTPConfigForm(request.POST, instance=smtp)
        if form.is_valid():
            form.save()
            messages.success(request, "SMTP server updated.")
            return redirect("smtp_list")
    else:
        form = SMTPConfigForm(instance=smtp)
    return render(request, "campaigns/smtp_form.html", {"form": form, "is_new": False, "smtp": smtp})


@login_required
def smtp_delete(request, pk):
    smtp = get_object_or_404(SMTPConfig, pk=pk)
    if request.method == "POST":
        smtp.delete()
        messages.success(request, "SMTP server deleted.")
    return redirect("smtp_list")


@login_required
def smtp_test(request, pk):
    smtp = get_object_or_404(SMTPConfig, pk=pk)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        if smtp.use_ssl:
            server = smtplib.SMTP_SSL(smtp.host, smtp.port, timeout=10)
        else:
            server = smtplib.SMTP(smtp.host, smtp.port, timeout=10)
            if smtp.use_tls:
                server.starttls()
        try:
            if smtp.username:
                server.login(smtp.username, smtp.get_password())
        finally:
            server.quit()
        messages.success(request, f"Connected to {smtp.host}:{smtp.port} successfully.")
    except Exception as exc:  # noqa: BLE001
        messages.error(request, f"Could not connect: {exc}")
    return redirect("smtp_list")


# ---------- Sending sessions ----------

@login_required
def session_list(request):
    return render(request, "campaigns/session_list.html", {"sessions": SendingSession.objects.all()})


@login_required
def session_create(request):
    if request.method == "POST":
        form = SendingSessionForm(request.POST, request.FILES)
        if form.is_valid():
            session = form.save(commit=False)
            session.created_by = request.user
            session.save()
            messages.success(request, "Session created. Now upload your recipient list.")
            return redirect("session_detail", pk=session.pk)
    else:
        form = SendingSessionForm()
    return render(request, "campaigns/session_form.html", {"form": form})


@login_required
def session_detail(request, pk):
    session = get_object_or_404(SendingSession, pk=pk)
    recipients = session.recipients.all()
    recipients_data = [
        {
            "name": r.name,
            "email": r.email,
            "status": r.get_status_display(),
            "status_raw": r.status,
            "last_sent_at": localtime(r.last_sent_at).strftime("%d %b %Y %H:%M") if r.last_sent_at else "",
            "error_message": r.error_message,
        }
        for r in recipients
    ]
    context = {
        "session": session,
        "counts": session.counts(),
        "recipients": recipients,
        "recipients_data": recipients_data,
        "upload_form": UploadForm(),
        "is_running": sender.is_running(session.pk),
        "can_resume": session.status in (SendingSession.STATUS_SENDING, SendingSession.STATUS_PAUSED)
        and not sender.is_running(session.pk)
        and session.heartbeat_is_stale,
    }
    return render(request, "campaigns/session_detail.html", context)


@login_required
def session_start(request, pk):
    session = get_object_or_404(SendingSession, pk=pk)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if sender.is_running(session.pk):
        messages.info(request, "This session is already sending.")
    elif not session.recipients.filter(status=Recipient.STATUS_PENDING).exists():
        messages.warning(request, "There are no pending recipients to send to. Upload a list first.")
    else:
        sender.start_sending(session.pk)
        messages.success(request, "Sending started.")
    return redirect("session_detail", pk=pk)


@login_required
def session_stop(request, pk):
    session = get_object_or_404(SendingSession, pk=pk)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    session.stop_requested = True
    session.save(update_fields=["stop_requested"])
    messages.info(request, "Stopping after the current email finishes sending.")
    return redirect("session_detail", pk=pk)


@login_required
def session_status_json(request, pk):
    session = get_object_or_404(SendingSession, pk=pk)
    return JsonResponse(
        {
            "status": session.status,
            "counts": session.counts(),
            "is_running": sender.is_running(session.pk),
        }
    )


# ---------- Upload / preview / confirm ----------

class _UploadedBytes(io.BytesIO):
    """A seekable BytesIO with a .name attribute, so excel_import.parse_spreadsheet
    (which expects a Django UploadedFile-like object) also works from bytes
    recovered from a hidden form field on the mapping step."""

    def __init__(self, name, data: bytes):
        super().__init__(data)
        self.name = name


def _render_preview(request, session, sheet):
    existing = {r.email: r for r in session.recipients.all()}
    rows = []
    for row in sheet.rows:
        if not row.valid:
            continue
        prior = existing.get(row.email)
        if prior and prior.status == Recipient.STATUS_SENT:
            kind = "already_sent"
            last_sent_at = prior.last_sent_at
        elif prior:
            kind = "retry"  # previously failed/pending — will be included automatically
            last_sent_at = prior.last_sent_at
        else:
            kind = "new"
            last_sent_at = None
        rows.append({"name": row.name, "email": row.email, "kind": kind, "last_sent_at": last_sent_at})

    invalid_rows = [r for r in sheet.rows if not r.valid]
    return render(
        request,
        "campaigns/upload_preview.html",
        {
            "session": session,
            "rows": rows,
            "invalid_rows": invalid_rows,
            "already_sent_count": sum(1 for r in rows if r["kind"] == "already_sent"),
            "new_count": sum(1 for r in rows if r["kind"] == "new"),
            "retry_count": sum(1 for r in rows if r["kind"] == "retry"),
        },
    )


@login_required
def session_upload(request, pk):
    session = get_object_or_404(SendingSession, pk=pk)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    form = UploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Please choose a file to upload.")
        return redirect("session_detail", pk=pk)

    uploaded = form.cleaned_data["file"]
    file_bytes = uploaded.read()
    sheet = excel_import.parse_spreadsheet(_UploadedBytes(uploaded.name, file_bytes))

    if not sheet.headers:
        messages.error(request, "That file appears to be empty or unreadable.")
        return redirect("session_detail", pk=pk)

    if not (sheet.name_column and sheet.email_column):
        # Can't confidently tell which column is which — ask the user.
        return render(
            request,
            "campaigns/upload_mapping.html",
            {
                "session": session,
                "headers": sheet.headers,
                "filename": uploaded.name,
                "file_b64": base64.b64encode(file_bytes).decode(),
            },
        )

    return _render_preview(request, session, sheet)


@login_required
def session_upload_mapped(request, pk):
    session = get_object_or_404(SendingSession, pk=pk)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    name_column = request.POST.get("name_column")
    email_column = request.POST.get("email_column")
    filename = request.POST.get("filename", "upload.xlsx")
    file_bytes = base64.b64decode(request.POST.get("file_b64", ""))

    if not name_column or not email_column or name_column == email_column:
        messages.error(request, "Please choose two different columns for name and email.")
        return redirect("session_detail", pk=pk)

    sheet = excel_import.parse_spreadsheet(
        _UploadedBytes(filename, file_bytes), name_column=name_column, email_column=email_column
    )
    return _render_preview(request, session, sheet)


@login_required
def session_upload_confirm(request, pk):
    session = get_object_or_404(SendingSession, pk=pk)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        row_count = int(request.POST.get("row_count", "0"))
    except ValueError:
        row_count = 0

    added = updated = unchanged = 0
    for i in range(row_count):
        name = request.POST.get(f"rows-{i}-name", "").strip()
        email = request.POST.get(f"rows-{i}-email", "").strip().lower()
        resend = request.POST.get(f"rows-{i}-resend") == "on"
        if not email:
            continue

        existing = session.recipients.filter(email=email).first()
        if not existing:
            Recipient.objects.create(session=session, name=name, email=email, status=Recipient.STATUS_PENDING)
            added += 1
        elif existing.status == Recipient.STATUS_SENT:
            if resend:
                existing.status = Recipient.STATUS_PENDING
                existing.name = name or existing.name
                existing.error_message = ""
                existing.save(update_fields=["status", "name", "error_message"])
                updated += 1
            else:
                unchanged += 1
        else:
            existing.status = Recipient.STATUS_PENDING
            existing.name = name or existing.name
            existing.error_message = ""
            existing.save(update_fields=["status", "name", "error_message"])
            updated += 1

    messages.success(
        request,
        f"{added} new recipient(s) added, {updated} queued to (re)send, "
        f"{unchanged} left as already sent.",
    )
    return redirect("session_detail", pk=pk)
