from django import forms

from campaigns.models import SMTPConfig, SendingSession


class SMTPConfigForm(forms.ModelForm):
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Leave blank when editing to keep the current password.",
    )

    class Meta:
        model = SMTPConfig
        fields = ["name", "host", "port", "use_tls", "use_ssl", "username", "from_name", "from_email"]

    def clean(self):
        cleaned = super().clean()
        needs_password = not self.instance.pk and cleaned.get("username")
        if needs_password and not cleaned.get("password"):
            self.add_error("password", "A password is required when a username is set.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            instance.set_password(password)
        if commit:
            instance.save()
        return instance


class _HiddenSyncedTextarea(forms.Textarea):
    """A Textarea that's kept off-screen and synced by JS from whichever
    editor (rich text or raw HTML) is active. Browsers refuse to run
    native "required" validation on a field that isn't focusable
    (display:none), which silently blocks form submission — so this
    widget opts out of the HTML5 required attribute entirely. Django's
    own server-side validation still enforces that the field isn't
    blank and renders {{ form.body.errors }} normally."""

    def use_required_attribute(self, initial):
        return False


class SendingSessionForm(forms.ModelForm):
    class Meta:
        model = SendingSession
        fields = ["name", "smtp_config", "subject", "body", "attachment"]
        widgets = {
            # The real field JS keeps in sync from whichever editor
            # (rich text or raw HTML) is active before the form submits.
            # Hidden from view, not disabled, so it still posts normally.
            "body": _HiddenSyncedTextarea(attrs={"id": "id_body_real", "class": "body-real-field", "style": "display:none;"}),
        }


class UploadForm(forms.Form):
    file = forms.FileField(help_text="An .xlsx or .csv file with a name column and an email column.")
