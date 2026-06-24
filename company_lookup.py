"""
company_lookup.py — легальная карточка компании по ИНН/ОГРН.

Источник: официальный API DaData (findById/party) → данные из открытого
реестра ЕГРЮЛ/ЕГРИП ФНС. Только юрлица и ИП, никаких частных лиц.

Требует:
  • DADATA_API_KEY в config.py (у тебя уже есть);
  • report_pdf.py рядом;
  • ensure_fonts() из bot.py вызвать перед генерацией (качает шрифты DejaVu).
"""

import re
from datetime import datetime

import httpx

from config import DADATA_API_KEY
from report_pdf import generate_report_pdf

STATUS_RU = {
    "ACTIVE":       "Действующее",
    "LIQUIDATING":  "Ликвидируется",
    "LIQUIDATED":   "Ликвидировано",
    "BANKRUPT":     "Банкротство",
    "REORGANIZING": "В процессе реорганизации",
}


async def fetch_company(inn_or_ogrn: str) -> dict | None:
    """Запрашивает компанию по ИНН/ОГРН через DaData. Возвращает data-словарь или None."""
    if not DADATA_API_KEY:
        return None
    q = re.sub(r'\D', '', inn_or_ogrn)
    if len(q) not in (10, 12, 13, 15):   # ИНН юр/ИП, ОГРН/ОГРНИП
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party",
                headers={
                    "Content-Type":  "application/json",
                    "Accept":        "application/json",
                    "Authorization": f"Token {DADATA_API_KEY}",
                },
                json={"query": q, "count": 1},
            )
            if r.status_code != 200:
                return None
            sug = r.json().get("suggestions") or []
            return sug[0]["data"] if sug else None
    except Exception:
        return None


def build_company_report(data: dict) -> bytes:
    """Собирает PDF-отчёт по компании из данных DaData."""
    name       = data.get("name") or {}
    name_full  = name.get("full_with_opf") or name.get("short_with_opf") or "—"
    name_short = name.get("short_with_opf") or name_full

    inn  = data.get("inn", "—")
    kpp  = data.get("kpp") or "—"
    ogrn = data.get("ogrn", "—")

    state  = data.get("state") or {}
    status = STATUS_RU.get(state.get("status", ""), state.get("status") or "—")
    reg_ts = state.get("registration_date")
    reg    = datetime.fromtimestamp(reg_ts / 1000).strftime("%d.%m.%Y") if reg_ts else "—"

    addr = (data.get("address") or {}).get("value") or "—"
    mgmt = data.get("management") or {}
    post = mgmt.get("post") or "Руководитель"
    director = mgmt.get("name") or "—"

    okveds = data.get("okveds") or []
    tags   = [f"{o.get('code','')} {o.get('name','')}".strip() for o in okveds[:10]]
    main_okved = next(
        (f"{o.get('code','')} {o.get('name','')}".strip() for o in okveds if o.get("main")),
        "",
    )

    summary = [
        ("Полное имя", name_full),
        ("ИНН / КПП",  f"{inn} / {kpp}"),
        ("ОГРН",       ogrn),
        ("Статус",     status),
    ]

    req_pairs = [(post, director), ("Адрес", addr), ("Дата регистрации", reg)]
    if main_okved:
        req_pairs.append(("Осн. вид деят.", main_okved))

    sections = [{"title": "Реквизиты", "subtitle": "ЕГРЮЛ", "pairs": req_pairs}]
    if tags:
        sections.append({"title": "Виды деятельности", "subtitle": "ОКВЭД", "tags": tags})
    sections.append({
        "title": "Источник",
        "note": "Сведения из открытого реестра ЕГРЮЛ (ФНС России), получены через "
                "официальный API DaData. Справочно, актуальны на дату формирования.",
    })

    return generate_report_pdf(
        title=name_short,
        subtitle=f"ИНН {inn} · {status}",
        summary_pairs=summary,
        sections=sections,
        brand=("Бизнес", "отчёт"),
        disclaimer="Отчёт сформирован автоматически по открытым данным ЕГРЮЛ. "
                   "Не является юридически значимым документом.",
    )


# ─── Пример интеграции в bot.py ────────────────────────────────────────
# from aiogram.types import BufferedInputFile
# from company_lookup import fetch_company, build_company_report
#
# @dp.message(S.inn)
# async def h_inn(msg: Message, state: FSMContext):
#     await state.clear()
#     use_request(msg.from_user.id)
#     log_search(msg.from_user.id, "company", msg.text.strip())
#     w = await msg.answer("🏢 Запрашиваю ЕГРЮЛ... ⏳")
#     await ensure_fonts()                       # функция уже есть в bot.py
#     data = await fetch_company(msg.text.strip())
#     await w.delete()
#     if not data:
#         await send_result(msg, "❌ Компания не найдена в реестре ЕГРЮЛ")
#         return
#     pdf = build_company_report(data)
#     fname = f"company_{re.sub(r'\\D', '', msg.text)[:15]}.pdf"
#     await msg.answer_document(
#         BufferedInputFile(pdf, filename=fname),
#         caption="📄 *Бизнес-отчёт* | данные ЕГРЮЛ",
#         parse_mode="Markdown",
#     )
#     rl = get_requests_left(msg.from_user.id)
#     await msg.answer(f"💰 Баланс: *{rl} запросов*", parse_mode="Markdown",
#                      reply_markup=kb_main(msg.from_user.id, rl))
