"""
web_lookup.py — отчёты по ДОМЕНУ и IP в том же светлом стиле.
Это инфраструктура, а не люди.

Источники (ключи не нужны):
  • RDAP (rdap.org) — регистрационные данные домена;
  • системный DNS-резолвер — A-запись;
  • ipapi.co — геолокация и ASN по IP.

Требует: report_pdf.py рядом; ensure_fonts() вызвать перед генерацией.
"""

import re
import socket
from datetime import datetime

import httpx

from report_pdf import generate_report_pdf


def _fmt_date(s: str | None) -> str:
    if not s:
        return "—"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except Exception:
        return str(s)[:10]


def _reverse_dns(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


# ─── IP ────────────────────────────────────────────────────────────────
async def fetch_ip(ip: str) -> dict | None:
    """Гео/ASN по IP через ipapi.co. None при ошибке."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://ipapi.co/{ip.strip()}/json/")
            if r.status_code == 200:
                d = r.json()
                if "error" not in d:
                    return d
    except Exception:
        pass
    return None


def build_ip_report(ip: str, data: dict,
                    brand=("Сетевой", "отчёт"), logo_path=None) -> bytes:
    rdns = _reverse_dns(ip)
    cc = data.get("country", "")
    country = data.get("country_name", "—")
    if cc:
        country = f"{country} ({cc})"
    summary = [
        ("IP-адрес",  ip),
        ("Страна",    country),
        ("Провайдер", data.get("org", "—")),
        ("ASN",       str(data.get("asn", "—"))),
    ]
    geo = [
        ("Город",        data.get("city", "—")),
        ("Регион",       data.get("region", "—")),
        ("Индекс",       str(data.get("postal", "—"))),
        ("Часовой пояс", data.get("timezone", "—")),
    ]
    lat, lon = data.get("latitude"), data.get("longitude")
    if lat and lon:
        geo.append(("Координаты", f"{lat}, {lon}"))

    net = [("Сеть", data.get("network", "—")), ("Версия IP", data.get("version", "—"))]
    if rdns:
        net.append(("Обратный DNS", rdns))

    sections = [
        {"title": "География", "subtitle": "ipapi.co", "pairs": geo},
        {"title": "Сеть",      "subtitle": "ASN / IP",  "pairs": net},
        {"title": "Источник",
         "note": "Геоданные — ipapi.co; обратный DNS — системный резолвер. "
                 "Точность геолокации IP ограничена и носит справочный характер."},
    ]
    sub = " · ".join(x for x in (data.get("org", ""), data.get("country_name", "")) if x)
    return generate_report_pdf(
        title=ip, subtitle=sub or "IP-адрес",
        summary_pairs=summary, sections=sections,
        brand=brand, logo_path=logo_path,
        disclaimer="Отчёт по открытым данным геолокации IP. Точность не гарантируется.",
    )


# ─── Домен ───────────────────────────────────────────────────────────────
async def fetch_domain(domain: str) -> dict:
    """RDAP + A-запись + гео хостинга. Возвращает агрегат (части могут быть None)."""
    domain = re.sub(r'^https?://', '', domain.strip()).split('/')[0].lower()
    out = {"domain": domain, "rdap": None, "ip": None, "geo": None}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(f"https://rdap.org/domain/{domain}",
                                 headers={"Accept": "application/rdap+json"})
            if r.status_code == 200:
                out["rdap"] = r.json()
    except Exception:
        pass
    try:
        out["ip"] = socket.gethostbyname(domain)
    except Exception:
        pass
    if out["ip"]:
        out["geo"] = await fetch_ip(out["ip"])
    return out


def _rdap_events(rdap: dict) -> dict:
    return {e.get("eventAction"): e.get("eventDate") for e in (rdap.get("events") or [])}


def _rdap_registrar(rdap: dict) -> str:
    for ent in rdap.get("entities") or []:
        if "registrar" in (ent.get("roles") or []):
            try:
                for item in ent["vcardArray"][1]:
                    if item[0] == "fn":
                        return item[3]
            except Exception:
                pass
            return ent.get("handle", "")
    return ""


def build_domain_report(info: dict,
                        brand=("Домен", "отчёт"), logo_path=None) -> bytes:
    domain = info["domain"]
    rdap = info.get("rdap") or {}
    ev = _rdap_events(rdap)
    registrar = _rdap_registrar(rdap) or "—"
    statuses = ", ".join(rdap.get("status") or []) or "—"
    ns = [n["ldhName"].lower() for n in (rdap.get("nameservers") or []) if n.get("ldhName")]

    summary = [
        ("Домен",       domain),
        ("Регистратор", registrar),
        ("Создан",      _fmt_date(ev.get("registration"))),
        ("Истекает",    _fmt_date(ev.get("expiration"))),
    ]
    reg = [
        ("Создан",   _fmt_date(ev.get("registration"))),
        ("Обновлён", _fmt_date(ev.get("last changed"))),
        ("Истекает", _fmt_date(ev.get("expiration"))),
        ("Статус",   statuses),
    ]
    sections = [{"title": "Регистрация", "subtitle": "RDAP", "pairs": reg}]
    if ns:
        sections.append({"title": "DNS-серверы", "tags": ns})

    geo = info.get("geo")
    if info.get("ip"):
        host = [("IP (A-запись)", info["ip"])]
        if geo:
            host += [
                ("Хостинг", geo.get("org", "—")),
                ("Страна",  geo.get("country_name", "—")),
                ("Город",   geo.get("city", "—")),
                ("ASN",     str(geo.get("asn", "—"))),
            ]
        sections.append({"title": "Хостинг", "subtitle": "A-запись · ipapi", "pairs": host})

    sections.append({"title": "Источник",
                     "note": "Регистрационные данные — протокол RDAP (rdap.org); "
                             "A-запись — DNS-резолвер; гео хостинга — ipapi.co. "
                             "Справочно, на дату формирования."})

    return generate_report_pdf(
        title=domain,
        subtitle=(f"Регистратор: {registrar}" if registrar != "—" else "Доменное имя"),
        summary_pairs=summary, sections=sections,
        brand=brand, logo_path=logo_path,
        disclaimer="Отчёт по открытым данным RDAP/DNS. Не является юридически значимым документом.",
    )


# ─── Пример интеграции в bot.py ──────────────────────────────────────────
# from aiogram.types import BufferedInputFile
# from web_lookup import fetch_ip, build_ip_report, fetch_domain, build_domain_report
#
# @dp.message(S.ip)
# async def h_ip(msg: Message, state: FSMContext):
#     await state.clear()
#     use_request(msg.from_user.id)
#     q = msg.text.strip()
#     log_search(msg.from_user.id, "ip_domain", q)
#     w = await msg.answer("🌐 Анализирую... ⏳")
#     await ensure_fonts()
#     is_ip = re.match(r'^\d{1,3}(\.\d{1,3}){3}$', q)
#     if is_ip:
#         data = await fetch_ip(q)
#         pdf = build_ip_report(q, data) if data else None
#     else:
#         info = await fetch_domain(q)
#         pdf = build_domain_report(info) if info.get("rdap") or info.get("ip") else None
#     await w.delete()
#     if not pdf:
#         await send_result(msg, "❌ Не удалось получить данные")
#         return
#     await msg.answer_document(
#         BufferedInputFile(pdf, filename=f"{re.sub(r'[^a-zA-Z0-9.]','_', q)[:20]}.pdf"),
#         caption="📄 *Отчёт по инфраструктуре*", parse_mode="Markdown")
#     rl = get_requests_left(msg.from_user.id)
#     await msg.answer(f"💰 Баланс: *{rl} запросов*", parse_mode="Markdown",
#                      reply_markup=kb_main(msg.from_user.id, rl))
