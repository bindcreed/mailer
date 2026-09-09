from django.contrib import admin

from campaigns.models import SMTPConfig, SendingSession, Recipient


@admin.register(SMTPConfig)
class SMTPConfigAdmin(admin.ModelAdmin):
    list_display = ("name", "host", "port", "from_email", "updated_at")


class RecipientInline(admin.TabularInline):
    model = Recipient
    extra = 0
    readonly_fields = ("last_sent_at", "attempts", "error_message")


@admin.register(SendingSession)
class SendingSessionAdmin(admin.ModelAdmin):
    list_display = ("name", "smtp_config", "status", "created_by", "created_at")
    list_filter = ("status",)
    inlines = [RecipientInline]


@admin.register(Recipient)
class RecipientAdmin(admin.ModelAdmin):
    list_display = ("email", "name", "session", "status", "last_sent_at")
    list_filter = ("status", "session")
    search_fields = ("email", "name")
