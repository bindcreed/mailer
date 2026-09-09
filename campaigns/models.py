from django.conf import settings
from django.db import models
from django.utils import timezone

from campaigns.services import crypto


class SMTPConfig(models.Model):
    """A reusable SMTP server configuration. The password is encrypted
    before it's stored and is only ever decrypted in memory at send time."""

    name = models.CharField(max_length=120, help_text="A label to recognise this server by, e.g. \"Company email\".")
    host = models.CharField(max_length=255)
    port = models.PositiveIntegerField(default=587)
    use_tls = models.BooleanField(default=True, help_text="STARTTLS — typical for port 587.")
    use_ssl = models.BooleanField(default=False, help_text="Implicit SSL — typical for port 465.")
    username = models.CharField(max_length=255, blank=True, help_text="Leave blank if your server doesn't require authentication.")
    password_encrypted = models.TextField(blank=True)
    from_name = models.CharField(max_length=120, blank=True)
    from_email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.host}:{self.port})"

    def set_password(self, raw_password: str):
        self.password_encrypted = crypto.encrypt(raw_password)

    def get_password(self) -> str:
        return crypto.decrypt(self.password_encrypted)


class SendingSession(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_SENDING = "sending"
    STATUS_PAUSED = "paused"
    STATUS_COMPLETED = "completed"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SENDING, "Sending"),
        (STATUS_PAUSED, "Paused"),
        (STATUS_COMPLETED, "Completed"),
    ]

    name = models.CharField(max_length=200)
    smtp_config = models.ForeignKey(SMTPConfig, on_delete=models.PROTECT, related_name="sessions")
    subject = models.CharField(max_length=255)
    body = models.TextField(
        help_text="HTML email body. Use {{name}} anywhere you want the recipient's name inserted."
    )
    attachment = models.FileField(upload_to="attachments/", blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    stop_requested = models.BooleanField(default=False)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def heartbeat_is_stale(self) -> bool:
        if not self.last_heartbeat:
            return True
        return (timezone.now() - self.last_heartbeat).total_seconds() > 60

    def counts(self):
        agg = self.recipients.values("status").order_by().annotate(n=models.Count("id"))
        result = {"pending": 0, "sent": 0, "failed": 0, "skipped": 0}
        for row in agg:
            result[row["status"]] = row["n"]
        result["total"] = sum(result.values())
        return result


class Recipient(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
        (STATUS_SKIPPED, "Skipped"),
    ]

    session = models.ForeignKey(SendingSession, on_delete=models.CASCADE, related_name="recipients")
    name = models.CharField(max_length=200, blank=True)
    email = models.EmailField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "email"]
        constraints = [
            models.UniqueConstraint(fields=["session", "email"], name="unique_recipient_per_session")
        ]

    def __str__(self):
        return f"{self.name} <{self.email}>"
