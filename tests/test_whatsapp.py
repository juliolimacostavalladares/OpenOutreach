"""Tests for WhatsApp enrichment and formatting."""
import pytest
from django.test import Client

from openoutfind.crm.models import Deal, DealState, Lead
from openoutreach.whatsapp import (
    STATE_TO_DDD,
    check_whatsapp_presence,
    deduce_lead_ddd,
    enrich_all_leads,
    enrich_company_via_brasilapi,
    enrich_lead_whatsapp,
    format_whatsapp,
    generate_deterministic_whatsapp,
    get_whatsapp_url,
    normalize_whatsapp_digits,
    validate_brazilian_phone,
)


@pytest.fixture(autouse=True)
def web_settings(settings):
    from openoutreach.web import settings as web
    settings.ROOT_URLCONF = web.ROOT_URLCONF
    settings.MIDDLEWARE = web.MIDDLEWARE
    settings.TEMPLATES = web.TEMPLATES
    settings.ALLOWED_HOSTS = ["testserver", "localhost"]


def test_normalize_whatsapp_digits():
    assert normalize_whatsapp_digits("+55 (11) 98765-4321") == "5511987654321"
    assert normalize_whatsapp_digits("5511987654321") == "5511987654321"
    assert normalize_whatsapp_digits("11987654321") == "5511987654321"
    assert normalize_whatsapp_digits("(21) 99876-5432") == "5521998765432"
    assert normalize_whatsapp_digits("98765-4321", default_ddd="31") == "5531987654321"
    assert normalize_whatsapp_digits("") == ""


def test_format_whatsapp():
    assert format_whatsapp("11987654321") == "+55 (11) 98765-4321"
    assert format_whatsapp("+55 11 98765-4321") == "+55 (11) 98765-4321"
    assert format_whatsapp("21998765432") == "+55 (21) 99876-5432"
    assert format_whatsapp("987654321", default_ddd="41") == "+55 (41) 98765-4321"
    assert format_whatsapp("") == ""


def test_get_whatsapp_url():
    assert get_whatsapp_url("+55 (11) 98765-4321") == "https://wa.me/5511987654321"
    assert get_whatsapp_url("21998765432") == "https://wa.me/5521998765432"
    assert get_whatsapp_url("") == ""


def test_deduce_lead_ddd(db):
    lead_sp = Lead.objects.create(full_name="Lead SP", source_fields={"contact_location_state": "SP"})
    assert deduce_lead_ddd(lead_sp) == "11"

    lead_mg = Lead.objects.create(full_name="Lead MG", source_fields={"contact_location_state": "MG"})
    assert deduce_lead_ddd(lead_mg) == "31"

    lead_rj = Lead.objects.create(full_name="Lead RJ", source_fields={"state": "RJ"})
    assert deduce_lead_ddd(lead_rj) == "21"

    lead_pr = Lead.objects.create(full_name="Lead PR", source_fields={"location": "Curitiba, Paraná, PR"})
    assert deduce_lead_ddd(lead_pr) == "41"

    lead_def = Lead.objects.create(full_name="Lead Default")
    assert deduce_lead_ddd(lead_def) == "11"


def test_generate_deterministic_whatsapp(db):
    lead = Lead.objects.create(full_name="Diretor Logística", source_fields={"contact_location_state": "MG"})
    p1 = generate_deterministic_whatsapp(lead)
    p2 = generate_deterministic_whatsapp(lead)
    assert p1 == p2
    assert p1.startswith("+55 (31) 9")
    assert len(p1) == 19  # +55 (31) 9XXXX-XXXX


def test_enrich_lead_whatsapp(db, monkeypatch):
    # 1. Lead without existing phone and web search finds a real phone
    lead = Lead.objects.create(full_name="Ana Silva", source_fields={"contact_location_state": "RS"})
    monkeypatch.setattr(
        "openoutreach.whatsapp.search_real_whatsapp_on_web",
        lambda *args, **kwargs: {
            "phone": "+55 (51) 99876-5432",
            "raw": "5551998765432",
            "valid": True,
            "type": "mobile",
            "source": "website_whatsapp",
            "confidence": "high",
            "reason": "Encontrado no site",
            "url": "https://wa.me/5551998765432",
        },
    )
    phone = enrich_lead_whatsapp(lead, use_web_search=True)
    lead.refresh_from_db()
    assert lead.source_fields.get("whatsapp") == "+55 (51) 99876-5432"
    assert lead.source_fields.get("whatsapp_source") == "website_whatsapp"
    assert phone == "+55 (51) 99876-5432"

    # 2. Lead where web search finds nothing: DOES NOT INVENT A NUMBER
    lead_empty = Lead.objects.create(full_name="Lead Fantasma", source_fields={"contact_location_state": "SP"})
    monkeypatch.setattr(
        "openoutreach.whatsapp.search_real_whatsapp_on_web",
        lambda *args, **kwargs: {
            "phone": "",
            "raw": "",
            "valid": False,
            "type": "none",
            "source": "not_found",
            "confidence": "none",
            "reason": "Nenhum número de contato público encontrado",
            "url": "",
        },
    )
    phone_empty = enrich_lead_whatsapp(lead_empty, use_web_search=True)
    lead_empty.refresh_from_db()
    assert phone_empty == ""
    assert lead_empty.source_fields.get("whatsapp") == ""
    assert lead_empty.source_fields.get("whatsapp_source") == "not_found"

    # 3. If lead already had a whatsapp, it preserves/formats it
    lead2 = Lead.objects.create(full_name="Bruno Castro", source_fields={"whatsapp": "11988887777"})
    phone2 = enrich_lead_whatsapp(lead2, use_web_search=False)
    assert phone2 == "+55 (11) 98888-7777"


def test_search_real_whatsapp_on_web(monkeypatch):
    from openoutreach.whatsapp import search_real_whatsapp_on_web

    # Test 1: Found on company website
    class DummyResp:
        status_code = 200
        text = '<html><body>Fale com nosso comercial: <a href="https://wa.me/5511987654321">WhatsApp</a></body></html>'

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: DummyResp())
    res = search_real_whatsapp_on_web({
        "contact_full_name": "Julio Lima",
        "company_name": "Motion Studio",
        "company_domain": "motionstudio.art",
        "contact_location_state": "SP",
    })
    assert res["valid"] is True
    assert res["phone"] == "+55 (11) 98765-4321"
    assert res["source"] == "website_whatsapp"
    assert res["url"] == "https://wa.me/5511987654321"

    # Test 2: Nothing found online -> does NOT invent numbers
    class DummyRespFail:
        status_code = 404
        text = "Not found"

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: DummyRespFail())
    monkeypatch.setattr("ddgs.DDGS.text", lambda *args, **kwargs: [])
    res_empty = search_real_whatsapp_on_web({
        "contact_full_name": "Contato Desconhecido",
        "company_name": "Empresa Sem Presenca",
        "contact_location_state": "SP",
    })
    assert res_empty["valid"] is False
    assert res_empty["phone"] == ""
    assert res_empty["source"] == "not_found"


def test_enrich_all_leads(db, monkeypatch):
    Lead.objects.create(full_name="Lead 1", source_fields={"contact_location_state": "SP", "whatsapp": "11977778888"})
    Lead.objects.create(full_name="Lead 2", source_fields={"contact_location_state": "RJ"})
    monkeypatch.setattr(
        "openoutreach.whatsapp.search_real_whatsapp_on_web",
        lambda *args, **kwargs: {
            "phone": "+55 (21) 98888-9999",
            "raw": "5521988889999",
            "valid": True,
            "type": "mobile",
            "source": "web_search_whatsapp_link",
            "confidence": "high",
            "reason": "Encontrado via web search",
            "url": "https://wa.me/5521988889999",
        },
    )
    res = enrich_all_leads(use_web_search=True)
    assert res["total"] >= 2
    assert res["already_had"] >= 1
    assert res["enriched"] >= 1
    for r in res["results"]:
        assert r["whatsapp"].startswith("+55 (")
        assert r["whatsapp_url"].startswith("https://wa.me/55")


def test_whatsapp_views_and_api(client, db, monkeypatch):
    lead1 = Lead.objects.create(
        full_name="Carlos Viana",
        email="carlos@example.com",
        source_fields={"contact_location_state": "MG", "whatsapp": "31987654321"},
    )
    lead2 = Lead.objects.create(
        full_name="Luciana Dias",
        email="",
        source_fields={"contact_location_state": "SP"},
    )
    Deal.objects.create(lead=lead1, state=DealState.QUALIFIED, reason="Fit perfeito")
    Deal.objects.create(lead=lead2, state=DealState.QUALIFIED, reason="Fit perfeito")

    # API enrich endpoint mock
    monkeypatch.setattr(
        "openoutreach.whatsapp.search_real_whatsapp_on_web",
        lambda *args, **kwargs: {
            "phone": "+55 (11) 99123-4567",
            "raw": "5511991234567",
            "valid": True,
            "type": "mobile",
            "source": "website_whatsapp",
            "confidence": "high",
            "reason": "Encontrado via website",
            "url": "https://wa.me/5511991234567",
        },
    )
    enrich_res = client.post("/api/enrich/whatsapp")
    assert enrich_res.status_code == 200
    assert enrich_res.json()["status"] == "ok"
    assert enrich_res.json()["enriched"] >= 1

    # Leads API returns whatsapp
    leads_res = client.get("/api/leads?status=whatsapp")
    assert leads_res.status_code == 200
    data = leads_res.json()
    assert data["total"] == 2
    assert data["rows"][0]["whatsapp"] != ""
    assert data["rows"][0]["whatsapp_url"].startswith("https://wa.me/55")

    # Dashboard API returns whatsapp stat
    dash_res = client.get("/api/dashboard")
    assert dash_res.status_code == 200
    assert dash_res.json()["stats"]["whatsapp"] == 2


def test_validate_brazilian_phone():
    # Valid mobile
    v1 = validate_brazilian_phone("+55 (11) 98765-4321")
    assert v1["valid"] is True
    assert v1["type"] == "mobile"
    assert v1["ddd"] == "11"

    # Valid landline
    v2 = validate_brazilian_phone("1133334444")
    assert v2["valid"] is True
    assert v2["type"] == "landline"

    # Invalid DDD (e.g. 00 or 90)
    v3 = validate_brazilian_phone("00987654321")
    assert v3["valid"] is False


def test_check_whatsapp_presence():
    # Valid mobile format verification
    res = check_whatsapp_presence("+55 (31) 98765-4321")
    assert res["valid"] is True
    assert res["confidence"] in ("medium", "high")
    assert "s.whatsapp.net" in res["jid"]

    # Invalid number
    inv = check_whatsapp_presence("123")
    assert inv["valid"] is False
    assert inv["confidence"] == "none"

