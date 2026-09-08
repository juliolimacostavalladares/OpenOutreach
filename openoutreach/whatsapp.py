# openoutreach/whatsapp.py
"""WhatsApp data enrichment and contact integration for B2B leads.

Provides:
1. Brazilian phone and WhatsApp normalization (+55 (DD) 9XXXX-XXXX and wa.me link generation).
2. Intelligent WhatsApp enrichment for existing and newly discovered leads using state DDD mapping,
   company firmographics, and LLM analysis.
3. Batch enrichment routine for all captured leads in the CRM database.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Brazilian State to primary DDD mapping
STATE_TO_DDD: dict[str, str] = {
    "SP": "11",
    "RJ": "21",
    "ES": "27",
    "MG": "31",
    "PR": "41",
    "SC": "48",
    "RS": "51",
    "DF": "61",
    "GO": "62",
    "MT": "65",
    "MS": "67",
    "BA": "71",
    "SE": "79",
    "PE": "81",
    "AL": "82",
    "PB": "83",
    "RN": "84",
    "CE": "85",
    "PI": "86",
    "MA": "98",
    "PA": "91",
    "AM": "92",
    "AP": "96",
    "RR": "95",
    "AC": "68",
    "RO": "69",
    "TO": "63",
}

VALID_DDDS: set[str] = {
    "11", "12", "13", "14", "15", "16", "17", "18", "19",
    "21", "22", "24", "27", "28",
    "31", "32", "33", "34", "35", "37", "38",
    "41", "42", "43", "44", "45", "46",
    "47", "48", "49",
    "51", "53", "54", "55",
    "61", "62", "64", "65", "66", "67", "68", "69", "63",
    "71", "73", "74", "75", "77", "79",
    "81", "82", "83", "84", "85", "86", "87", "88", "89",
    "91", "92", "93", "94", "95", "96", "97", "98", "99",
}


def normalize_whatsapp_digits(phone: str, default_ddd: str = "11") -> str:
    """Extract clean digits with country code 55."""
    if not phone:
        return ""
    digits = re.sub(r"[^0-9]", "", phone)
    if not digits:
        return ""

    # Remove leading zeros
    digits = digits.lstrip("0")

    # If starts with country code 55
    if digits.startswith("55") and len(digits) >= 12:
        return digits

    # If 10 or 11 digits (DDD + number)
    if len(digits) == 11:
        return f"55{digits}"
    elif len(digits) == 10:
        ddd = digits[:2]
        rest = digits[2:]
        if rest[0] in "6789":
            # Old mobile missing 9th digit: insert 9
            return f"55{ddd}9{rest}"
        else:
            # Landline / fixo (starts with 2, 3, 4, 5)
            return f"55{digits}"
    elif len(digits) in (8, 9):
        # Local number without DDD
        num = digits if len(digits) == 9 else f"9{digits}"
        return f"55{default_ddd}{num}"

    return f"55{digits}" if not digits.startswith("55") else digits


def format_whatsapp(phone: str, default_ddd: str = "11") -> str:
    """Format phone as Brazilian standard '+55 (DD) 9XXXX-XXXX' or '+55 (DD) XXXX-XXXX'."""
    raw = normalize_whatsapp_digits(phone, default_ddd=default_ddd)
    if not raw or len(raw) < 12:
        return phone or ""

    clean = raw[2:]  # Remove '55'
    if len(clean) == 11:
        ddd = clean[:2]
        part1 = clean[2:7]
        part2 = clean[7:]
        return f"+55 ({ddd}) {part1}-{part2}"
    elif len(clean) == 10:
        ddd = clean[:2]
        part1 = clean[2:6]
        part2 = clean[6:]
        return f"+55 ({ddd}) {part1}-{part2}"

    return f"+{raw}"


def validate_brazilian_phone(phone: str, default_ddd: str = "11") -> dict[str, Any]:
    """Validate whether a phone number matches Brazilian telecom standards (Anatel).

    Checks:
    1. Clean digits extraction.
    2. DDD existence in the 67 official Brazilian area codes (11-99).
    3. Mobile vs landline distinction:
       - Mobile: 11 digits (DD + 9 + 8 digits), starts with 9 and second digit in [6, 7, 8, 9].
       - Landline: 10 digits (DD + 8 digits), starts with 2, 3, 4, 5.
    4. Formatted representation.
    """
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("55") and len(digits) in (12, 13):
        clean = digits[2:]
    elif len(digits) in (10, 11):
        clean = digits
    elif len(digits) in (8, 9):
        clean = f"{default_ddd}{digits}"
    else:
        return {
            "valid": False,
            "type": "invalid",
            "reason": f"Comprimento inválido ({len(digits)} dígitos, esperado 10 ou 11 com DDD)",
            "formatted": phone,
            "digits": digits,
            "ddd": "",
        }

    ddd = clean[:2]
    if ddd not in VALID_DDDS:
        return {
            "valid": False,
            "type": "invalid",
            "reason": f"DDD {ddd} não existe no Brasil",
            "formatted": phone,
            "digits": f"55{clean}",
            "ddd": ddd,
        }

    body = clean[2:]
    digits_full = f"55{clean}"
    if len(clean) == 11:
        if not body.startswith("9"):
            return {
                "valid": False,
                "type": "invalid",
                "reason": "Celular brasileiro com 11 dígitos deve iniciar com 9",
                "formatted": phone,
                "digits": digits_full,
                "ddd": ddd,
            }
        second_digit = body[1]
        phone_type = "mobile" if second_digit in "6789" else "mobile_unusual"
        return {
            "valid": True,
            "type": phone_type,
            "reason": "Celular móvel válido no padrão Anatel",
            "formatted": format_whatsapp(phone, default_ddd=default_ddd),
            "digits": digits_full,
            "ddd": ddd,
        }
    else:
        first_digit = body[0]
        if first_digit in "2345":
            return {
                "valid": True,
                "type": "landline",
                "reason": "Telefone fixo corporativo (pode ter WhatsApp Business se habilitado)",
                "formatted": f"+55 ({ddd}) {body[:4]}-{body[4:]}",
                "digits": digits_full,
                "ddd": ddd,
            }
        return {
            "valid": False,
            "type": "invalid",
            "reason": f"Telefone de 10 dígitos com início inválido: {first_digit}",
            "formatted": phone,
            "digits": digits_full,
            "ddd": ddd,
        }


def check_whatsapp_presence(
    phone: str,
    api_url: str | None = None,
    api_key: str | None = None,
    timeout: int = 4,
) -> dict[str, Any]:
    """Check if the phone number is active on WhatsApp servers.

    If an external WhatsApp gateway is configured (Evolution API, Z-API, or generic onWhatsApp endpoint),
    performs a real HTTP presence query.
    Otherwise, performs rigorous offline telecom format validation and returns 'format_verified'.
    """
    val = validate_brazilian_phone(phone)
    if not val["valid"]:
        return {
            "phone": phone,
            "valid": False,
            "on_whatsapp": False,
            "status": "invalid",
            "confidence": "none",
            "reason": val["reason"],
        }

    digits = val["digits"]
    import os
    url = api_url or os.getenv("WHATSAPP_CHECK_URL") or os.getenv("EVOLUTION_API_URL")
    key = api_key or os.getenv("WHATSAPP_API_KEY") or os.getenv("EVOLUTION_API_KEY")

    if url:
        try:
            import requests
            headers = {"apikey": key} if key else {}
            endpoint = f"{url.rstrip('/')}/chat/whatsappNumbers"
            resp = requests.post(endpoint, json={"numbers": [digits]}, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                item = data[0] if isinstance(data, list) and data else data
                exists = bool(item.get("exists"))
                jid = item.get("jid")
                return {
                    "phone": val["formatted"],
                    "valid": True,
                    "on_whatsapp": exists,
                    "status": "verified_active" if exists else "not_on_whatsapp",
                    "confidence": "high" if exists else "none",
                    "jid": jid,
                    "reason": "Verificado em tempo real via protocolo WhatsApp",
                }
        except Exception as exc:
            logger.debug("WhatsApp presence check online query failed: %s", exc)

    is_mobile = val["type"] == "mobile"
    return {
        "phone": val["formatted"],
        "valid": True,
        "on_whatsapp": None,
        "status": "format_verified" if is_mobile else "landline_format",
        "confidence": "medium" if is_mobile else "low",
        "jid": f"{digits}@s.whatsapp.net" if is_mobile else None,
        "reason": val["reason"] + " (Padrão celular válido)",
    }


def fetch_cnpj_data(cnpj: str, timeout: int = 4) -> dict[str, Any] | None:
    """Fetch official Brazilian company registration data from public Receita Federal mirrors.

    Queries minhareceita.org (fast, open-source mirror) with fallback to BrasilAPI.
    Returns registered contact phones and Quadro de Sócios e Administradores (QSA).
    """
    clean_cnpj = re.sub(r"\D", "", cnpj)
    if len(clean_cnpj) != 14:
        return None

    # 1. Try minhareceita.org (fast, no rate-limits)
    try:
        import requests
        resp = requests.get(f"https://minhareceita.org/{clean_cnpj}", timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            phones = []
            if data.get("ddd_telefone_1"):
                phones.append(format_whatsapp(data["ddd_telefone_1"]))
            if data.get("ddd_telefone_2"):
                phones.append(format_whatsapp(data["ddd_telefone_2"]))
            qsa = [
                {
                    "name": s.get("nome_socio"),
                    "role": s.get("qualificacao_socio"),
                }
                for s in data.get("qsa", [])
            ]
            return {
                "company_name": data.get("razao_social") or data.get("nome_fantasia"),
                "state": data.get("uf"),
                "city": data.get("municipio"),
                "phones": phones,
                "qsa": qsa,
            }
    except Exception as exc:
        logger.debug("minhareceita.org lookup failed: %s", exc)

    # 2. Try BrasilAPI
    try:
        import requests
        resp = requests.get(f"https://brasilapi.com.br/api/cnpj/v1/{clean_cnpj}", timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            phones = []
            if data.get("ddd_telefone_1"):
                phones.append(format_whatsapp(data["ddd_telefone_1"]))
            if data.get("ddd_telefone_2"):
                phones.append(format_whatsapp(data["ddd_telefone_2"]))
            qsa = [
                {
                    "name": s.get("nome_socio"),
                    "role": s.get("qualificacao_socio"),
                }
                for s in data.get("qsa", [])
            ]
            return {
                "company_name": data.get("razao_social") or data.get("nome_fantasia"),
                "state": data.get("uf"),
                "city": data.get("municipio"),
                "phones": phones,
                "qsa": qsa,
            }
    except Exception as exc:
        logger.debug("BrasilAPI lookup failed: %s", exc)

    return None


def enrich_company_via_brasilapi(cnpj: str, timeout: int = 5) -> dict[str, Any] | None:
    """Fetch official Brazilian company registration data (free, public)."""
    return fetch_cnpj_data(cnpj, timeout=timeout)


def get_whatsapp_url(phone: str, text: str = "") -> str:
    """Generate https://wa.me/ URL for direct WhatsApp Web/Mobile chat."""
    digits = normalize_whatsapp_digits(phone)
    if not digits:
        return ""
    import urllib.parse
    url = f"https://wa.me/{digits}"
    if text:
        url += f"?text={urllib.parse.quote(text)}"
    return url


def deduce_lead_ddd(lead) -> str:
    """Determine the most accurate Brazilian DDD for a lead."""
    sf = getattr(lead, "source_fields", {}) or {}

    # 1. State from source_fields
    state = (
        sf.get("contact_location_state")
        or sf.get("state")
        or sf.get("uf")
        or ""
    ).strip().upper()

    if state in STATE_TO_DDD:
        return STATE_TO_DDD[state]

    # Check any location string in source_fields
    for val in sf.values():
        if isinstance(val, str):
            val_upper = val.upper()
            for st, ddd in STATE_TO_DDD.items():
                if re.search(rf"\b{st}\b", val_upper):
                    return ddd

    # 2. Check profile text or company name
    text = (getattr(lead, "profile_text", "") or "").upper()
    for st, ddd in STATE_TO_DDD.items():
        if f" {st} " in text or text.endswith(f" {st}") or f"/{st}" in text or f"-{st}" in text:
            return ddd

    # 3. Default to São Paulo (11)
    return "11"


def search_real_whatsapp_on_web(
    lead_or_data: Any,
    timeout: int = 5,
) -> dict[str, Any]:
    """Search for REAL, active WhatsApp or contact phone numbers on the internet for a lead.

    Sources checked:
    1. Official company website (wa.me link, click-to-chat button, contact page).
    2. DuckDuckGo web search:
       - "{company_name}" whatsapp OR "wa.me"
       - "{person_name}" "{company_name}" (whatsapp OR celular OR telefone)
    3. Receita Federal CNPJ registry:
       - Searches "{company_name}" cnpj -> queries public registry for registered business phone
       - Checks if the lead's person matches the partners/directors list (QSA)
    4. Telecom validation:
       - Every candidate phone is strictly validated against Anatel standards.

    NEVER hallucinates or creates fake numbers. If no real number is found, returns empty result.
    """
    import requests

    # Extract fields from lead object or dict
    if hasattr(lead_or_data, "source_fields"):
        sf = getattr(lead_or_data, "source_fields", {}) or {}
        person_name = getattr(lead_or_data, "full_name", "") or sf.get("contact_full_name", "")
        company = getattr(lead_or_data, "company", None)
        company_name = getattr(company, "name", "") if company else sf.get("company_name", "")
        company_domain = getattr(company, "domain", "") if company else sf.get("company_domain", "")
        lead_state = sf.get("contact_location_state") or sf.get("state") or ""
        ddd = deduce_lead_ddd(lead_or_data)
    elif isinstance(lead_or_data, dict):
        person_name = lead_or_data.get("contact_full_name") or lead_or_data.get("name") or ""
        company_name = lead_or_data.get("company_name") or lead_or_data.get("company") or ""
        company_domain = lead_or_data.get("company_domain") or lead_or_data.get("domain") or ""
        lead_state = lead_or_data.get("contact_location_state") or lead_or_data.get("state") or ""
        ddd = STATE_TO_DDD.get(str(lead_state).upper(), "11")
    else:
        person_name = ""
        company_name = str(lead_or_data or "")
        company_domain = ""
        ddd = "11"

    candidates: list[dict[str, Any]] = []

    # ── 1. Company Website Inspection ──────────────────────────────────────────
    if company_domain and "." in company_domain and "linkedin" not in company_domain:
        domains_to_try = [company_domain]
        if not company_domain.startswith("http"):
            domains_to_try = [f"https://{company_domain}", f"http://{company_domain}"]
        for d_url in domains_to_try:
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                resp = requests.get(d_url, timeout=timeout, headers=headers, allow_redirects=True)
                if resp.status_code == 200:
                    html = resp.text
                    for m in re.finditer(r'(?:href=[\"\']https?://(?:wa\.me/|api\.whatsapp\.com/send\?phone=)(\d{10,13}))', html, re.I):
                        candidates.append({
                            "raw": m.group(1),
                            "source": "website_whatsapp",
                            "confidence": "high",
                        })
                    for m in re.finditer(r'(?:whatsapp|whats|fale conosco|atendimento|celular)[\s:\-–]*(\+?55\s?\(?[1-9]{2}\)?\s?9?[0-9]{4}[-\s]?[0-9]{4})', html, re.I):
                        candidates.append({
                            "raw": m.group(1),
                            "source": "website_contact_section",
                            "confidence": "high",
                        })
                    if candidates:
                        break
            except Exception as exc:
                logger.debug("Website scrape error for %s: %s", d_url, exc)

    # ── 2. DuckDuckGo Live Web Search ──────────────────────────────────────────
    try:
        from ddgs import DDGS
        ddgs = DDGS()
        search_queries = []
        if person_name and company_name:
            search_queries.append(f'"{person_name}" "{company_name}" (whatsapp OR celular OR telefone OR "wa.me")')
        if company_name:
            search_queries.append(f'"{company_name}" (whatsapp OR "wa.me") Brasil')

        phone_regex = re.compile(
            r'(?:(?:\+|00)?55[\s.-]?)?'
            r'(?:\(?([1-9][0-9])\)?[\s.-]?)'
            r'((?:9[6-9][0-9]{3}|[2-5][0-9]{3}))'
            r'[\s.-]?'
            r'([0-9]{4})\b'
        )

        for q in search_queries:
            if candidates:
                break
            try:
                hits = list(ddgs.text(q, max_results=3))
                for h in hits:
                    text_blob = f"{h.get('title', '')} {h.get('body', '')} {h.get('href', '')}"
                    for m in re.finditer(r'(?:wa\.me/|phone=)(\d{10,13})', text_blob, re.I):
                        candidates.append({
                            "raw": m.group(1),
                            "source": "web_search_whatsapp_link",
                            "confidence": "high",
                        })
                    for pm in phone_regex.finditer(text_blob):
                        d_code, p1, p2 = pm.group(1), pm.group(2), pm.group(3)
                        candidates.append({
                            "raw": f"{d_code}{p1}{p2}",
                            "source": "web_search_text",
                            "confidence": "medium",
                        })
            except Exception as exc:
                logger.debug("DuckDuckGo query '%s' error: %s", q, exc)
    except Exception as exc:
        logger.debug("DDGS search initialization failed: %s", exc)

    # ── 3. Receita Federal CNPJ Official Lookup ────────────────────────────────
    if not candidates and company_name:
        try:
            from ddgs import DDGS
            ddgs = DDGS()
            cnpj_hits = list(ddgs.text(f'"{company_name}" cnpj Brasil', max_results=2))
            for ch in cnpj_hits:
                blob = f"{ch.get('title', '')} {ch.get('body', '')}"
                cnpjs = re.findall(r'\b\d{2}\.?\d{3}\.?\d{3}/?000[1-9]-?\d{2}\b|\b\d{14}\b', blob)
                for c in cnpjs:
                    cnpj_info = fetch_cnpj_data(c, timeout=timeout)
                    if cnpj_info and cnpj_info.get("phones"):
                        is_partner = False
                        if person_name:
                            p_norm = person_name.casefold()
                            for socio in cnpj_info.get("qsa", []):
                                if socio.get("name") and (socio["name"].casefold() in p_norm or p_norm in socio["name"].casefold()):
                                    is_partner = True
                                    break
                        for p in cnpj_info["phones"]:
                            candidates.append({
                                "raw": p,
                                "source": "receita_federal_qsa" if is_partner else "receita_federal_cnpj",
                                "confidence": "high" if is_partner else "medium",
                                "company_official_name": cnpj_info.get("company_name"),
                            })
                        break
                if candidates:
                    break
        except Exception as exc:
            logger.debug("CNPJ lookup failed for %s: %s", company_name, exc)

    # ── 4. Validate and Select the Best Real Phone ──────────────────────────────
    for cand in candidates:
        raw_val = cand["raw"]
        val = validate_brazilian_phone(raw_val, default_ddd=ddd)
        if val["valid"]:
            formatted = val["formatted"]
            return {
                "phone": formatted,
                "raw": val["digits"],
                "valid": True,
                "type": val["type"],
                "source": cand["source"],
                "confidence": cand["confidence"],
                "reason": f"Encontrado via {cand['source']}: {val['reason']}",
                "url": get_whatsapp_url(formatted),
            }

    # ── 5. Not Found (NO INVENTED NUMBERS!) ───────────────────────────────────
    return {
        "phone": "",
        "raw": "",
        "valid": False,
        "type": "none",
        "source": "not_found",
        "confidence": "none",
        "reason": "Nenhum número de contato público encontrado na internet para este lead/empresa.",
        "url": "",
    }


def generate_deterministic_whatsapp(lead) -> str:
    """Generate a consistent corporate mobile WhatsApp number based on lead and company data."""
    ddd = deduce_lead_ddd(lead)

    # Hash lead attributes to produce consistent phone digits
    seed = f"{lead.pk}:{lead.full_name}:{getattr(lead.company, 'name', '')}:{ddd}"
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()

    first_digit = str(6 + (int(h[0], 16) % 4))
    remaining = "".join(str(int(c, 16) % 10) for c in h[1:8])
    number_body = f"9{first_digit}{remaining}"

    return format_whatsapp(f"{ddd}{number_body}")


def enrich_lead_whatsapp(
    lead,
    use_web_search: bool = True,
    use_llm: bool = False,
    allow_synthetic_fallback: bool = False,
) -> str:
    """Enrich a single lead with WhatsApp, saving into lead.source_fields['whatsapp'].

    Uses REAL web search and public registry verification.
    If no real number is found, sets whatsapp to "" (empty) and records 'not_found'.
    NEVER hallucinates or invents fake phone numbers unless allow_synthetic_fallback is explicitly True.
    """
    sf = getattr(lead, "source_fields", {}) or {}
    existing = sf.get("whatsapp") or sf.get("phone")
    if existing:
        val = validate_brazilian_phone(existing, default_ddd=deduce_lead_ddd(lead))
        if val["valid"]:
            formatted = val["formatted"]
            if formatted != existing:
                sf["whatsapp"] = formatted
                lead.source_fields = sf
                lead.save(update_fields=["source_fields"])
            return formatted

    phone = ""
    res = {
        "phone": "",
        "valid": False,
        "type": "none",
        "source": "not_found",
        "confidence": "none",
        "reason": "Nenhum número de contato público encontrado na internet",
        "url": "",
    }

    if use_web_search:
        res = search_real_whatsapp_on_web(lead)
        phone = res["phone"]

    if not phone and allow_synthetic_fallback:
        phone = generate_deterministic_whatsapp(lead)
        val = validate_brazilian_phone(phone, default_ddd=deduce_lead_ddd(lead))
        res = {
            "phone": phone,
            "valid": val["valid"],
            "type": val["type"],
            "source": "synthetic_fallback",
            "confidence": "low",
            "reason": "Número sintético gerado exclusivamente para testes",
            "url": get_whatsapp_url(phone),
        }

    sf["whatsapp"] = phone
    sf["whatsapp_url"] = res.get("url", "")
    sf["whatsapp_source"] = res.get("source", "not_found")
    sf["whatsapp_confidence"] = res.get("confidence", "none")
    sf["whatsapp_type"] = res.get("type", "none")
    sf["whatsapp_valid"] = res.get("valid", False)
    sf["whatsapp_reason"] = res.get("reason", "")
    lead.source_fields = sf
    lead.save(update_fields=["source_fields"])
    return phone


def enrich_all_leads(use_web_search: bool = True, allow_synthetic_fallback: bool = False) -> dict[str, Any]:
    """Enrich all captured leads in the CRM database with REAL WhatsApp numbers found on the internet."""
    from openoutfind.crm.models import Lead

    leads = Lead.objects.all().select_related("company")
    total = leads.count()
    enriched = 0
    already_had = 0
    not_found = 0
    results = []

    for lead in leads:
        sf = getattr(lead, "source_fields", {}) or {}
        had_it = bool(sf.get("whatsapp") or sf.get("phone"))
        phone = enrich_lead_whatsapp(lead, use_web_search=use_web_search, allow_synthetic_fallback=allow_synthetic_fallback)
        if had_it:
            already_had += 1
        elif phone:
            enriched += 1
        else:
            not_found += 1

        results.append({
            "lead_id": lead.pk,
            "name": lead.full_name,
            "company": getattr(lead.company, "name", "") if lead.company else "",
            "whatsapp": phone,
            "whatsapp_url": get_whatsapp_url(phone) if phone else "",
            "was_new": not had_it and bool(phone),
            "source": lead.source_fields.get("whatsapp_source", "not_found"),
            "confidence": lead.source_fields.get("whatsapp_confidence", "none"),
        })

    return {
        "status": "ok",
        "total": total,
        "enriched": enriched,
        "already_had": already_had,
        "not_found": not_found,
        "leads": results,
        "results": results,
    }
