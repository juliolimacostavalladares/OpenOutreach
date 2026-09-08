"""Tests for Free Discovery, WhatsApp integration, and SiteConfig adapters."""
from unittest.mock import MagicMock
import pytest

from openoutreach.adapters import (
    free_discovery_search,
)
from openoutreach.config.models import SiteConfig
from openoutreach.web.forms import IntegrationForm
from openoutreach.web.views import public_config


def test_free_discovery_search_returns_valid_page(db):
    site_config = SiteConfig.load()
    site_config.product_docs = "Software de gestão logística para distribuidoras"
    site_config.campaign_target = "Diretores de logística de distribuidoras no Brasil"
    site_config.save()

    page = free_discovery_search({}, limit=3)
    assert len(page.leads) >= 3
    for lead in page.leads:
        assert "contact_full_name" in lead
        assert "contact_job_title" in lead
        assert "company_name" in lead
        assert "company_domain" in lead
        assert lead["contact_linkedin_profile_url"].startswith("https://www.linkedin.com/in/")


def test_site_config_export_with_model_prefix(db):
    config = SiteConfig.load()
    config.ai_model = "cbai/minimax-m3"
    config.llm_api_base = "http://localhost:20128/v1"
    config.save()

    env = config.export()
    assert env["OPENOUTFIND_AI_MODEL"] == "openai_compatible:cbai/minimax-m3"
    assert env["OUTSEND_AI_MODEL"] == "openai_compatible:cbai/minimax-m3"


def test_integration_form_normalizes_ai_model(db):
    form_data = {
        "ai_model": "cbai/minimax-m3",
        "llm_api_base": "http://localhost:20128/v1",
        "operator_name": "Julio",
        "operator_country_code": "BR",
    }
    form = IntegrationForm(data=form_data)
    assert form.is_valid(), form.errors
    cleaned = form.cleaned_data
    assert cleaned["ai_model"] == "openai_compatible:cbai/minimax-m3"
    assert cleaned["operator_country_code"] == "br"


def test_public_config_reports_free_provider(db):
    config = SiteConfig.load()
    config.bettercontact_api_key = ""
    config.save()

    pub = public_config(config)
    assert pub["free_provider_active"] is True

    # If BetterContact key is added
    config.bettercontact_api_key = "bc_real_key"
    config.save()
    pub2 = public_config(config)
    assert pub2["free_provider_active"] is False


def test_job_wait_captures_stderr():
    from openoutreach.web import jobs

    mock_proc = MagicMock()
    mock_proc.communicate.return_value = ("", "Error: Missing configuration for AI model")
    mock_proc.returncode = 1

    jobs._process = mock_proc
    jobs._state = {"status": "running"}

    jobs._wait(mock_proc)

    assert jobs._state["status"] == "failed"
    assert jobs._state["exit_code"] == 1
    assert "Missing configuration" in jobs._state["error"]


@pytest.mark.django_db
def test_idempotent_deal_creation():
    from openoutfind.crm.models import Lead, Deal, DealState
    from openoutfind.core.db.leads import promote_lead_to_deal
    from openoutfind.core.db.deals import _create_deal

    lead = Lead.objects.create(profile_url="https://www.linkedin.com/in/test-idempotent-lead")

    # First promote creates deal
    d1 = promote_lead_to_deal(lead.profile_url, reason="Primeira qualificação")
    assert d1.state == DealState.QUALIFIED
    assert d1.reason == "Primeira qualificação"
    assert Deal.objects.filter(lead=lead).count() == 1

    # Second promote updates deal instead of raising IntegrityError
    d2 = promote_lead_to_deal(lead.profile_url, reason="Segunda qualificação atualizada")
    assert d2.pk == d1.pk
    assert d2.reason == "Segunda qualificação atualizada"
    assert Deal.objects.filter(lead=lead).count() == 1

    # _create_deal also updates instead of failing UNIQUE constraint
    d3 = _create_deal(lead=lead, state=DealState.FAILED, outcome="wrong_fit", reason="Rejeitado")
    assert d3.pk == d1.pk
    assert d3.state == DealState.FAILED
    assert Deal.objects.filter(lead=lead).count() == 1
