from django import forms

from openoutreach.config.models import SiteConfig


class CampaignForm(forms.ModelForm):
    product_docs = forms.CharField(max_length=20000)
    campaign_target = forms.CharField(max_length=20000)
    whatsapp_template = forms.CharField(max_length=20000, required=False, widget=forms.Textarea)

    class Meta:
        model = SiteConfig
        fields = ["product_docs", "campaign_target", "whatsapp_template", "booking_link"]

    def clean_booking_link(self):
        value = self.cleaned_data["booking_link"]
        if value:
            forms.URLField().clean(value)
            if not value.startswith("https://"):
                raise forms.ValidationError("Use um link HTTPS.")
        return value


class IntegrationForm(forms.ModelForm):
    """Blank secret fields mean keep the saved value; secrets are never returned."""
    secret_fields = ("llm_api_key", "bettercontact_api_key")

    class Meta:
        model = SiteConfig
        fields = [
            "ai_model", "llm_api_key", "llm_api_base", "bettercontact_api_key",
            "operator_name", "operator_country_code", "accepted_legal_notice",
        ]

    def clean(self):
        cleaned = super().clean()
        for field in self.secret_fields:
            if not cleaned.get(field):
                cleaned[field] = getattr(self.instance, field)
        country = cleaned.get("operator_country_code", "").upper()
        if country:
            import pytz
            if country not in pytz.country_names:
                self.add_error("operator_country_code", "Use um país válido, como BR ou PT.")
        cleaned["operator_country_code"] = country.lower()

        # Normalize ai_model with openai_compatible: prefix if needed
        ai_model = cleaned.get("ai_model", "").strip()
        llm_base = cleaned.get("llm_api_base", "").strip() or getattr(self.instance, "llm_api_base", "")
        if ai_model and ":" not in ai_model:
            if llm_base or any(ai_model.startswith(p) for p in ("cbai/", "ollama/", "bzl/", "cx/")):
                cleaned["ai_model"] = f"openai_compatible:{ai_model}"

        return cleaned
