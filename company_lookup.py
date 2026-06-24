"""
company_lookup.py — расширенная проверка контрагента (юрлица / ИП).

Источники (легальные, открытые):
  • DaData findById/party  → реквизиты ЕГРЮЛ/ЕГРИП (нужен DADATA_API_KEY);
  • ГИР БО (bo.nalog.gov.ru) → бухгалтерская отчётность (выручка/прибыль/активы),
    без ключа, best-effort — если недоступно, отчёт собирается без финблока.

Только организации, не частные лица.
Требует: report_pdf.py рядом; ensure_fonts() вызвать перед генерацией.
"""

import re
from datetime import datetime, date

import httpx

from config import DADATA_API_KEY
from report_pdf import generate_report_pdf

STATUS_RU = {
    "ACTIVE":       "Действующее",
    "LIQUIDATING":  "В стадии ликвидации",
    "LIQUIDATED":   "Ликвидировано",
    "BANKRUPT":     "Банкротство",
    "REORGANIZING": "В стадии реорганизации",
}

UA = "Mozilla/5.0 (compatible; CounterpartyBot/1.0)"


# ─── утилиты ────────────────────────────────────────────────────────────
def _ts_to_date(ms) -> str:
    if not ms:
        return "—"
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%d.%m.%Y")
    except Exception:
        return "—"


def _money(v) -> str:
    """Число → '1 234 567 ₽'."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{n:,.0f}".replace(",", " ") + " ₽"


def _years_since(ms) -> str:
    if not ms:
        return "—"
    try:
        d = datetime.fromtimestamp(ms / 1000).date()
        days = (date.today() - d).days
        y, m = days // 365, (days % 365) // 30
        if y >= 1:
            return f"{y} г. {m} мес."
        return f"{m} мес."
    except Exception:
        return "—"


def _scan(obj, key):
    """Рекурсивно ищет первое значение по ключу в JSON (для разбора ГИР БО)."""
    if isinstance(obj, dict):
        if key in obj and obj[key] not in (None, ""):
            return obj[key]
        for v in obj.values():
            r = _scan(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _scan(v, key)
            if r is not None:
                return r
    return None


# ─── DaData: реквизиты компании ─────────────────────────────────────────
async def fetch_company(inn_or_ogrn: str) -> dict | None:
    if not DADATA_API_KEY:
        return None
    q = re.sub(r'\D', '', inn_or_ogrn)
    if len(q) not in (10, 12, 13, 15):
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


# ─── ГИР БО: финансовая отчётность (best-effort, без ключа) ──────────────
async def fetch_financials(inn: str) -> dict | None:
    inn = re.sub(r'\D', '', inn)
    headers = {"Accept": "application/json", "User-Agent": UA,
               "Referer": "https://bo.nalog.gov.ru/"}
    try:
        async with httpx.AsyncClient(timeout=12, headers=headers,
                                     follow_redirects=True) as client:
            s = await client.get(
                "https://bo.nalog.gov.ru/nbo/organizations/search",
                params={"query": inn, "page": 0},
            )
            if s.status_code != 200:
                return None
            js = s.json()
            items = js.get("content") or js.get("items") or []
            if not items:
                return None
            org_id = items[0].get("id")
            if not org_id:
                return None

            periods = await client.get(
                f"https://bo.nalog.gov.ru/nbo/organizations/{org_id}/bfo/"
            )
            if periods.status_code != 200:
                return None
            plist = periods.json()
            if not isinstance(plist, list) or not plist:
                return None
            latest = sorted(plist, key=lambda x: str(x.get("period", "")))[-1]
            bfo_id = latest.get("id")
            year = latest.get("period")
            if not bfo_id:
                return None

            detail = await client.get(f"https://bo.nalog.gov.ru/nbo/bfo/{bfo_id}")
            if detail.status_code != 200:
                return None
            d = detail.json()
            return {
                "year":    year,
                "revenue": _scan(d, "current2110"),   # выручка
                "profit":  _scan(d, "current2400"),   # чистая прибыль
                "assets":  _scan(d, "current1600"),   # активы (баланс)
                "capital": _scan(d, "current1300"),   # капитал и резервы
            }
    except Exception:
        return None


# ─── изображения из открытых источников (best-effort) ──────────────────
async def fetch_logo(domain: str) -> bytes | None:
    """Логотип/favicon сайта компании. Только бренд организации, не люди."""
    if not domain:
        return None
    domain = re.sub(r'^https?://', '', domain.strip()).split('/')[0]
    urls = [
        f"https://logo.clearbit.com/{domain}",
        f"https://www.google.com/s2/favicons?domain={domain}&sz=128",
    ]
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True,
                                     headers={"User-Agent": UA}) as client:
            for u in urls:
                r = await client.get(u)
                ct = r.headers.get("content-type", "")
                if r.status_code == 200 and "image" in ct and len(r.content) > 200:
                    return r.content
    except Exception:
        pass
    return None


async def fetch_location_map(data: dict) -> bytes | None:
    """Статичная карта по координатам адреса из ЕГРЮЛ (место, не люди)."""
    addr = (data.get("address") or {}).get("data") or {}
    lat, lon = addr.get("geo_lat"), addr.get("geo_lon")
    if not (lat and lon):
        return None
    url = ("https://staticmap.openstreetmap.de/staticmap.php"
           f"?center={lat},{lon}&zoom=16&size=620x300&maptype=mapnik"
           f"&markers={lat},{lon},red-pushpin")
    try:
        async with httpx.AsyncClient(timeout=12, headers={"User-Agent": UA}) as client:
            r = await client.get(url)
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and "image" in ct and len(r.content) > 1000:
                return r.content
    except Exception:
        pass
    return None


# ─── сборка отчёта ──────────────────────────────────────────────────────
def build_company_spec(data: dict, finance: dict | None = None,
                       logo: bytes | None = None, map_img: bytes | None = None,
                       brand=("Контр", "агент")) -> dict:
    """Готовит универсальный «spec» отчёта (для PDF и для HTML)."""
    name      = data.get("name") or {}
    name_full = name.get("full_with_opf") or name.get("short_with_opf") or "—"
    name_shrt = name.get("short_with_opf") or name_full

    inn  = data.get("inn", "—")
    kpp  = data.get("kpp") or "—"
    ogrn = data.get("ogrn", "—")
    opf  = (data.get("opf") or {}).get("full") or (data.get("opf") or {}).get("short") or "—"

    state   = data.get("state") or {}
    status  = STATUS_RU.get(state.get("status", ""), state.get("status") or "—")
    reg_ms  = state.get("registration_date")
    act_ms  = state.get("actuality_date")
    liq_ms  = state.get("liquidation_date")

    addr = (data.get("address") or {}).get("value") or "—"
    mgmt = data.get("management") or {}
    director = mgmt.get("name") or "—"
    post     = mgmt.get("post") or "Руководитель"
    disq     = mgmt.get("disqualified")

    cap = data.get("capital") or {}
    cap_val = _money(cap.get("value")) if cap.get("value") else None
    emp = data.get("employee_count")
    branches = data.get("branch_count")

    # ── Краткая сводка ──
    summary = [
        ("Полное имя",  name_full),
        ("ИНН / КПП",   f"{inn} / {kpp}"),
        ("ОГРН",        ogrn),
        ("Статус",      f"{status}" + (f" (с {_ts_to_date(liq_ms or act_ms)})" if status != "Действующее" else "")),
    ]

    sections = []

    # ── Реквизиты ──
    req = [
        ("ОПФ",              opf),
        (post,               director),
        ("Адрес",            addr),
        ("Дата регистрации", _ts_to_date(reg_ms)),
        ("Актуальность",     _ts_to_date(act_ms)),
    ]
    sections.append({"title": "Реквизиты", "subtitle": "ЕГРЮЛ", "pairs": req})

    # ── Капитал и масштаб ──
    scale = []
    if cap_val:
        scale.append(("Уставный капитал", cap_val))
    if emp not in (None, ""):
        scale.append(("Среднесписочная числ.", str(emp)))
    if branches not in (None, "", 0):
        scale.append(("Филиалов", str(branches)))
    if scale:
        sections.append({"title": "Капитал и масштаб", "pairs": scale})

    # ── Учредители (если DaData вернула) ──
    founders = data.get("founders") or []
    if founders:
        fp = []
        for f in founders[:8]:
            fname = (f.get("name") or f.get("fio") or
                     (f.get("inn") and f"ИНН {f['inn']}") or "—")
            share = f.get("share") or {}
            sv = share.get("value")
            suffix = ""
            if sv is not None:
                stype = share.get("type")
                suffix = f" — {sv}%" if stype == "PERCENT" else f" — доля {sv}"
            fp.append(("Учредитель", f"{fname}{suffix}"))
        sections.append({"title": "Учредители", "subtitle": "ЕГРЮЛ", "pairs": fp})

    # ── Виды деятельности ──
    okveds = data.get("okveds") or []
    main_ok = ""
    for o in okveds:
        if o.get("main"):
            main_ok = f"{o.get('code','')} {o.get('name','')}".strip()
            break
    if not main_ok and data.get("okved"):
        main_ok = str(data["okved"])
    act_pairs = [("Основной", main_ok or "—")]
    sections.append({"title": "Виды деятельности", "subtitle": "ОКВЭД", "pairs": act_pairs})
    if okveds:
        tags = [f"{o.get('code','')} {o.get('name','')}".strip() for o in okveds[:12] if not o.get("main")]
        if tags:
            sections.append({"title": "Доп. виды деятельности", "tags": tags})

    # ── Регистрации и коды ──
    auth = data.get("authorities") or {}
    docs = data.get("documents") or {}
    codes = []
    fts_reg = (auth.get("fts_registration") or {}).get("name")
    fts_rep = (auth.get("fts_report") or {}).get("name")
    pf  = (auth.get("pf") or {}).get("name")
    sif = (auth.get("sif") or {}).get("name")
    if fts_reg: codes.append(("ИФНС (регистрация)", fts_reg))
    if fts_rep: codes.append(("ИФНС (отчётность)", fts_rep))
    if pf:      codes.append(("ПФР", pf))
    if sif:     codes.append(("ФСС", sif))
    for label, key in [("ОКПО", "okpo"), ("ОКТМО", "oktmo"),
                       ("ОКАТО", "okato"), ("ОКОГУ", "okogu"), ("ОКФС", "okfs")]:
        if data.get(key):
            codes.append((label, str(data[key])))
    if codes:
        sections.append({"title": "Регистрации и коды", "pairs": codes})

    # ── Финансы (ГИР БО) ──
    fin = finance or {}
    if any(fin.get(k) for k in ("revenue", "profit", "assets", "capital")):
        y = fin.get("year", "")
        fp = []
        if fin.get("revenue") is not None: fp.append((f"Выручка ({y})", _money(fin["revenue"])))
        if fin.get("profit")  is not None: fp.append((f"Чистая прибыль ({y})", _money(fin["profit"])))
        if fin.get("assets")  is not None: fp.append((f"Активы ({y})", _money(fin["assets"])))
        if fin.get("capital") is not None: fp.append((f"Капитал и резервы ({y})", _money(fin["capital"])))
        sections.append({"title": "Финансовые показатели", "subtitle": "ГИР БО (ФНС)", "pairs": fp})

    # ── Признаки для проверки ──
    flags = [
        ("Возраст компании", _years_since(reg_ms)),
        ("Статус",           status),
        ("Дисквалификация рук-ля", "ВЫЯВЛЕНА" if disq else "не выявлена"),
    ]
    if (data.get("branch_type") or "") == "BRANCH":
        flags.append(("Тип", "Филиал / обособленное подразделение"))
    sections.append({"title": "Признаки для проверки", "pairs": flags})

    # ── Источник ──
    src = ("Реквизиты — открытый реестр ЕГРЮЛ/ЕГРИП (ФНС) через официальный API DaData. "
           "Финансовые показатели — ГИР БО (bo.nalog.gov.ru). Карта — OpenStreetMap по "
           "адресу из ЕГРЮЛ. Сведения справочные, на дату формирования.")

    # ── Местоположение (карта по адресу) ──
    if map_img:
        sections.append({"title": "Местоположение", "subtitle": "адрес из ЕГРЮЛ",
                         "image": map_img, "caption": addr})

    sections.append({"title": "Источники", "note": src})

    return {
        "title":         name_shrt,
        "subtitle":      f"ИНН {inn} · {status}",
        "summary_pairs": summary,
        "sections":      sections,
        "brand":         brand,
        "hero_image":    logo,
        "nav":           True,
        "disclaimer":    "Отчёт сформирован автоматически по открытым данным ЕГРЮЛ и ГИР БО. "
                         "Не является юридически значимым документом и не заменяет выписку из ЕГРЮЛ.",
    }


def build_company_report(data: dict, finance: dict | None = None,
                         logo: bytes | None = None, map_img: bytes | None = None,
                         brand=("Контр", "агент")) -> bytes:
    """PDF-версия отчёта."""
    spec = build_company_spec(data, finance=finance, logo=logo, map_img=map_img, brand=brand)
    return generate_report_pdf(**spec)
