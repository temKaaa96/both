"""
OSINT Telegram Bot v2
Стек: Python 3.10+, aiogram 3, SQLite
"""

import asyncio
import logging
import sqlite3
import re
import os
import io
import tempfile
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery,
    BufferedInputFile
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import httpx

from config import BOT_TOKEN, BOT_USERNAME, ADMIN_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ─── Пакеты запросов ─────────────────────────────────────────────────────────
PACKAGES = {
    "pack_20":  {"requests": 20,  "stars": 50,  "label": "20 запросов"},
    "pack_50":  {"requests": 50,  "stars": 100, "label": "50 запросов"},
    "pack_100": {"requests": 100, "stars": 180, "label": "100 запросов"},
    "pack_250": {"requests": 250, "stars": 350, "label": "250 запросов"},
}

SHERLOCK_SITES = {
    "ВКонтакте":  "https://vk.com/{}",
    "Instagram":  "https://www.instagram.com/{}",
    "Twitter/X":  "https://twitter.com/{}",
    "TikTok":     "https://www.tiktok.com/@{}",
    "GitHub":     "https://github.com/{}",
    "YouTube":    "https://www.youtube.com/@{}",
    "Telegram":   "https://t.me/{}",
    "Reddit":     "https://www.reddit.com/user/{}",
    "Pinterest":  "https://www.pinterest.com/{}",
    "Twitch":     "https://www.twitch.tv/{}",
    "Steam":      "https://steamcommunity.com/id/{}",
    "LinkedIn":   "https://www.linkedin.com/in/{}",
    "Flickr":     "https://www.flickr.com/people/{}",
    "Tumblr":     "https://{}.tumblr.com",
    "SoundCloud": "https://soundcloud.com/{}",
    "Behance":    "https://www.behance.net/{}",
    "Dribbble":   "https://dribbble.com/{}",
    "Medium":     "https://medium.com/@{}",
    "DeviantArt": "https://www.deviantart.com/{}",
    "Spotify":    "https://open.spotify.com/user/{}",
}

OPERATORS_RU = {
    '900':'МТС','901':'МТС','902':'МТС','903':'МТС','904':'МТС',
    '905':'МТС','906':'МТС','908':'МТС','909':'МТС',
    '910':'МегаФон','911':'МегаФон','912':'МТС','913':'МегаФон',
    '914':'МегаФон','915':'МТС','916':'МТС','917':'МегаФон',
    '918':'МегаФон','919':'МТС','920':'МегаФон','921':'МегаФон',
    '922':'МТС','923':'МегаФон','924':'МегаФон','925':'МТС',
    '926':'МТС','927':'МТС','928':'МегаФон','929':'МТС',
    '930':'МТС','931':'МегаФон','932':'МТС','933':'МегаФон',
    '934':'МегаФон','936':'МТС','937':'МегаФон','938':'МегаФон','939':'МегаФон',
    '950':'МегаФон','951':'МТС','952':'МТС','953':'МегаФон','958':'МегаФон',
    '960':'Билайн','961':'Билайн','962':'Билайн','963':'Билайн',
    '964':'Билайн','965':'Билайн','966':'МТС','967':'Билайн',
    '968':'МТС','969':'Билайн','970':'Tele2','971':'Билайн','977':'МТС',
    '980':'МТС','981':'МегаФон','982':'МТС','983':'МТС',
    '984':'МегаФон','985':'МТС','986':'МегаФон','987':'МегаФон',
    '988':'МегаФон','989':'МегаФон',
    '991':'Tele2','992':'Tele2','993':'Tele2','994':'Tele2',
    '995':'Tele2','996':'Tele2','997':'МТС','999':'МТС',
}

# ─── База данных ─────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect("users.db")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id         INTEGER PRIMARY KEY,
            username        TEXT,
            requests_left   INTEGER DEFAULT 3,
            total_purchased INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS searches (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type    TEXT,
            query   TEXT,
            created TEXT
        );
    """)
    con.commit()
    con.close()

def get_user(user_id: int) -> dict | None:
    con = sqlite3.connect("users.db")
    row = con.execute(
        "SELECT user_id, username, requests_left, total_purchased FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()
    con.close()
    if not row:
        return None
    return dict(zip(["user_id","username","requests_left","total_purchased"], row))

def upsert_user(user_id: int, username: str):
    con = sqlite3.connect("users.db")
    con.execute(
        "INSERT OR IGNORE INTO users (user_id, username, requests_left) VALUES (?,?,3)",
        (user_id, username)
    )
    con.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
    con.commit()
    con.close()

def can_search(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    user = get_user(user_id)
    return user is not None and user["requests_left"] > 0

def use_request(user_id: int):
    if user_id == ADMIN_ID:
        return
    con = sqlite3.connect("users.db")
    con.execute(
        "UPDATE users SET requests_left = MAX(0, requests_left - 1) WHERE user_id=?",
        (user_id,)
    )
    con.commit()
    con.close()

def add_requests(user_id: int, count: int):
    con = sqlite3.connect("users.db")
    con.execute(
        "UPDATE users SET requests_left = requests_left + ?, total_purchased = total_purchased + ? WHERE user_id=?",
        (count, count, user_id)
    )
    con.commit()
    con.close()

def log_search(user_id: int, search_type: str, query: str):
    con = sqlite3.connect("users.db")
    con.execute(
        "INSERT INTO searches (user_id, type, query, created) VALUES (?,?,?,?)",
        (user_id, search_type, query, datetime.now().isoformat())
    )
    con.commit()
    con.close()

def get_stats() -> dict:
    con = sqlite3.connect("users.db")
    total = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    paid  = con.execute("SELECT COUNT(*) FROM users WHERE total_purchased > 0").fetchone()[0]
    searches = con.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
    top = con.execute(
        "SELECT type, COUNT(*) FROM searches GROUP BY type ORDER BY COUNT(*) DESC LIMIT 5"
    ).fetchall()
    con.close()
    return {"total": total, "paid": paid, "searches": searches, "top": top}

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def get_requests_left(user_id: int) -> int:
    if is_admin(user_id):
        return 999
    user = get_user(user_id)
    return user["requests_left"] if user else 0

# ─── FSM ─────────────────────────────────────────────────────────────────────
class S(StatesGroup):
    username    = State()
    phone       = State()
    email       = State()
    image       = State()
    vk          = State()
    car         = State()
    ip          = State()
    inn         = State()
    telegram    = State()
    full_search = State()

# ─── Клавиатуры ──────────────────────────────────────────────────────────────
def kb_main(user_id: int, rl: int) -> InlineKeyboardMarkup:
    bal = "∞" if is_admin(user_id) else str(rl)
    rows = []
    if is_admin(user_id):
        rows.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    rows += [
        [
            InlineKeyboardButton(text="🔍 Ник / Username",   callback_data="s_username"),
            InlineKeyboardButton(text="📱 Телефон",          callback_data="s_phone"),
        ],
        [
            InlineKeyboardButton(text="📧 Email",            callback_data="s_email"),
            InlineKeyboardButton(text="👤 ВКонтакте",        callback_data="s_vk"),
        ],
        [
            InlineKeyboardButton(text="💬 Telegram",         callback_data="s_telegram"),
            InlineKeyboardButton(text="🚗 Авто по номеру",   callback_data="s_car"),
        ],
        [
            InlineKeyboardButton(text="🌐 IP / Домен",       callback_data="s_ip"),
            InlineKeyboardButton(text="🏢 ИНН / ОГРН",       callback_data="s_inn"),
        ],
        [InlineKeyboardButton(text="🖼 Reverse Image Search", callback_data="s_image")],
        [InlineKeyboardButton(text="🗂 Полное досье (всё сразу)", callback_data="s_full")],
        [InlineKeyboardButton(text=f"💎 Купить запросы  |  💰 Баланс: {bal}", callback_data="buy_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_buy() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="20 запросов — 50 ⭐",   callback_data="buy_pack_20")],
        [InlineKeyboardButton(text="50 запросов — 100 ⭐",  callback_data="buy_pack_50")],
        [InlineKeyboardButton(text="100 запросов — 180 ⭐", callback_data="buy_pack_100")],
        [InlineKeyboardButton(text="250 запросов — 350 ⭐", callback_data="buy_pack_250")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])

def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_main")]
    ])

def kb_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🎁 Выдать запросы", callback_data="admin_give")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")],
    ])

# ─── OSINT функции ───────────────────────────────────────────────────────────
async def fn_username(username: str) -> str:
    found, errors = [], []
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        tasks = {n: client.get(u.format(username), headers={"User-Agent":"Mozilla/5.0"})
                 for n, u in SHERLOCK_SITES.items()}
        for name, task in tasks.items():
            try:
                r = await task
                url = SHERLOCK_SITES[name].format(username)
                if r.status_code == 200:
                    found.append(f"✅ [{name}]({url})")
                else:
                    errors.append(name)
            except:
                errors.append(name)
    result = f"🔍 *Поиск по нику:* `{username}`\n\n"
    result += (f"📍 Найдено на *{len(found)}* сайтах:\n" + "\n".join(found)) if found else "❌ Аккаунты не найдены"
    result += f"\n\n⬜ Не найдено/недоступно: {len(errors)}"
    return result

async def fn_phone(phone: str) -> str:
    clean = re.sub(r'[^\d+]', '', phone)
    if not clean.startswith('+'):
        clean = '+7' + clean[1:] if clean.startswith('8') else '+' + clean

    result = f"📱 *Анализ номера:* `{clean}`\n\n"

    operator, country = "Неизвестно", "Неизвестно"
    if clean.startswith('+7') and len(clean) == 12:
        code = clean[2:5]
        operator = OPERATORS_RU.get(code, 'Неизвестный оператор')
        country = "🇷🇺 Россия"
    elif clean.startswith('+380'): country = "🇺🇦 Украина"
    elif clean.startswith('+375'): country = "🇧🇾 Беларусь"
    elif clean.startswith('+7'):   country = "🇷🇺🇰🇿 Россия/Казахстан"
    else: country = "🌍 Международный"

    result += f"🌍 Страна: {country}\n"
    if operator != "Неизвестно":
        result += f"📡 Оператор: {operator}\n"
    result += f"🔢 Формат: {clean}\n\n"

    # Пробуем найти в открытых источниках
    num_digits = re.sub(r'[^\d]', '', clean)
    result += f"🔎 *Поиск личности:*\n"
    result += f"• [NumBuster](https://numbuster.com/number/{clean.replace('+','')}) — имя из контактов\n"
    result += f"• [GetContact Web](https://getcontact.com/ru/{clean.replace('+','')}) — теги и имена\n"
    result += f"• [Truecaller](https://www.truecaller.com/search/ru/{num_digits}) — глобальный поиск\n"
    result += f"• [PhoneBook](https://phonebook.cz/phone/{clean.replace('+','')}) — телефонная книга\n\n"
    result += f"📧 *Связанные аккаунты:*\n"
    result += f"• [ВКонтакте](https://vk.com/search?c[section]=people&c[phone]={num_digits}) — поиск по номеру\n"
    result += f"• [Telegram](https://t.me/+{num_digits}) — если есть аккаунт"
    return result

async def fn_email(email: str) -> str:
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return "❌ Неверный формат email"
    domain = email.split('@')[1]
    known = {
        'gmail.com':'Google Gmail','yahoo.com':'Yahoo','mail.ru':'Mail.ru',
        'yandex.ru':'Яндекс','outlook.com':'Microsoft','hotmail.com':'Hotmail',
        'icloud.com':'Apple iCloud','rambler.ru':'Rambler',
        'bk.ru':'Mail.ru','list.ru':'Mail.ru','inbox.ru':'Mail.ru',
    }
    provider = known.get(domain, domain)
    result = (
        f"📧 *Анализ email:* `{email}`\n\n"
        f"📮 Провайдер: {provider}\n\n"
        f"🔐 *Проверка утечек:*\n"
        f"• [HaveIBeenPwned](https://haveibeenpwned.com/account/{email}) — международные базы\n"
        f"• [DeHashed](https://dehashed.com/search?query={email}) — поиск в утечках\n"
        f"• [LeakCheck](https://leakcheck.io) — российские утечки\n"
        f"• [BreachDirectory](https://breachdirectory.org) — пароли из утечек\n\n"
        f"🔍 *Поиск аккаунтов:*\n"
        f"• [ВКонтакте](https://vk.com/search?c[section]=people&c[email]={email}) — по email\n"
        f"• [Google](https://www.google.com/search?q=%22{email}%22) — упоминания в интернете"
    )
    return result

async def fn_vk(query: str) -> str:
    q_enc = query.strip().replace(' ', '+')
    is_nick = ' ' not in query.strip()
    result = f"👤 *Поиск ВКонтакте:* `{query}`\n\n"

    if is_nick:
        nick = query.strip().lstrip('@')
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                r = await client.get(f"https://vk.com/{nick}", headers={"User-Agent":"Mozilla/5.0"})
                if r.status_code == 200 and 'page_not_found' not in str(r.url):
                    text = r.text
                    name_m = re.search(r'<title>(.*?)\s*[|—]', text)
                    id_m = re.search(r'"id":(\d+)', text)
                    closed = 'ProfileBlocked' in text or 'profile_closed' in text
                    followers_m = re.search(r'"followers_count":(\d+)', text)

                    result += f"✅ *Профиль найден!*\n"
                    if name_m:
                        result += f"👤 Имя: {name_m.group(1).strip()}\n"
                    if id_m:
                        result += f"🆔 ID: {id_m.group(1)}\n"
                    result += f"🔗 Ссылка: vk.com/{nick}\n"
                    if followers_m:
                        result += f"👥 Подписчиков: {followers_m.group(1)}\n"
                    result += f"🔒 Закрытый: {'Да' if closed else 'Нет'}\n\n"
                else:
                    result += f"❌ Профиль @{nick} не найден\n\n"
        except:
            result += f"⚠️ Не удалось проверить профиль\n\n"

    result += f"🔗 *Поиск вручную:*\n"
    result += f"• [Поиск людей](https://vk.com/search?c[section]=people&c[q]={q_enc})\n"
    if is_nick:
        result += f"• [Прямая ссылка](https://vk.com/{query.strip().lstrip('@')})\n"
    result += f"• [Поиск в России](https://vk.com/search?c[section]=people&c[q]={q_enc}&c[country]=1)"
    return result

async def fn_telegram(query: str) -> str:
    username = query.strip().lstrip('@')
    result = f"💬 *Поиск Telegram:* `@{username}`\n\n"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.get(f"https://t.me/{username}", headers={"User-Agent":"Mozilla/5.0"})
            if r.status_code == 200:
                text = r.text
                name_m = re.search(r'<meta property="og:title" content="(.*?)"', text)
                desc_m = re.search(r'<meta property="og:description" content="(.*?)"', text)
                is_channel = 'tgme_page_extra' in text

                result += f"✅ *Найдено!*\n"
                if name_m:
                    result += f"📛 Имя: {name_m.group(1)}\n"
                result += f"🔗 Ссылка: t.me/{username}\n"
                result += f"📌 Тип: {'Канал/Группа' if is_channel else 'Пользователь'}\n"
                if desc_m and desc_m.group(1):
                    desc = desc_m.group(1)[:200]
                    result += f"📝 Описание: {desc}\n"
            else:
                result += f"❌ @{username} не найден или аккаунт скрыт\n"
    except:
        result += f"⚠️ Не удалось проверить\n"

    result += f"\n🔗 *Дополнительно:*\n"
    result += f"• [Открыть профиль](https://t.me/{username})\n"
    result += f"• [TGStat](https://tgstat.ru/channel/@{username}) — статистика канала\n"
    result += f"• [Telemetr](https://telemetr.io/@{username}) — аналитика"
    return result

async def fn_car(plate: str) -> str:
    plate_clean = re.sub(r'[^А-ЯA-Z0-9]', '', plate.upper().replace(' ',''))
    result = f"🚗 *Проверка авто:* `{plate}`\n\n"
    result += f"🔎 *Открытые источники:*\n"
    result += f"• [ГИБДД РФ](https://xn--90adear.xn--p1ai/check/auto) — официальная проверка\n"
    result += f"• [Автокод](https://avtokod.mos.ru) — Москва и МО\n"
    result += f"• [ЕАИСТО](https://eaisto.info) — техосмотр\n"
    result += f"• [ReestrAuto](https://reestr.auto/api/) — история владельцев\n\n"

    # Пробуем получить данные через открытый API
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.mvd.ru/api/v1/plate/{plate_clean}",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if r.status_code == 200:
                data = r.json()
                result += f"✅ Данные получены:\n{data}\n"
    except:
        pass

    result += (
        f"📋 *Что можно узнать:*\n"
        f"• История владельцев\n"
        f"• ДТП и штрафы\n"
        f"• Ограничения ГИБДД\n"
        f"• Техосмотр\n\n"
        f"💡 Введи номер на сайтах выше для полной проверки"
    )
    return result

async def fn_ip(query: str) -> str:
    is_ip = re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', query.strip())
    result = f"🌐 *{'IP-адрес' if is_ip else 'Домен'}:* `{query}`\n\n"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if is_ip:
                r = await client.get(f"https://ipapi.co/{query.strip()}/json/")
            else:
                domain = query.strip().replace('http://','').replace('https://','').split('/')[0]
                r = await client.get(f"https://ipapi.co/{domain}/json/")

            if r.status_code == 200:
                data = r.json()
                if 'error' not in data:
                    result += f"📍 Страна: {data.get('country_name','—')}\n"
                    result += f"🏙 Город: {data.get('city','—')}\n"
                    result += f"📮 Регион: {data.get('region','—')}\n"
                    result += f"🌐 IP: {data.get('ip','—')}\n"
                    result += f"🏢 Провайдер: {data.get('org','—')}\n"
                    result += f"📡 ASN: {data.get('asn','—')}\n"
                    lat = data.get('latitude','')
                    lon = data.get('longitude','')
                    if lat and lon:
                        result += f"🗺 Координаты: {lat}, {lon}\n"
                        result += f"• [Открыть на карте](https://maps.google.com/?q={lat},{lon})\n"
                else:
                    result += f"❌ Не удалось определить\n"
    except Exception as e:
        result += f"⚠️ Ошибка запроса\n"

    # Дополнительные инструменты
    target = query.strip()
    result += f"\n🔍 *Дополнительно:*\n"
    result += f"• [Whois](https://who.is/whois/{target}) — владелец домена\n"
    result += f"• [VirusTotal](https://www.virustotal.com/gui/domain/{target}) — проверка репутации\n"
    result += f"• [Shodan](https://www.shodan.io/host/{target}) — открытые порты\n"
    result += f"• [AbuseIPDB](https://www.abuseipdb.com/check/{target}) — жалобы на IP"
    return result

async def fn_inn(query: str) -> str:
    inn_clean = re.sub(r'[^\d]', '', query.strip())
    result = f"🏢 *Проверка ИНН/ОГРН:* `{inn_clean}`\n\n"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Открытый API ФНС
            r = await client.get(
                f"https://egrul.nalog.ru/search-result/{inn_clean}",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if r.status_code == 200:
                data = r.json()
                if data.get('rows'):
                    row = data['rows'][0]
                    result += f"✅ *Найдено в реестре ФНС:*\n"
                    result += f"🏢 Название: {row.get('n','—')}\n"
                    result += f"📍 Адрес: {row.get('a','—')}\n"
                    result += f"🔢 ИНН: {row.get('i','—')}\n"
                    result += f"📋 ОГРН: {row.get('o','—')}\n"
                    result += f"📅 Дата регистрации: {row.get('r','—')}\n"
                    status = row.get('sf','')
                    result += f"⚡ Статус: {'✅ Действующее' if not status else '❌ ' + status}\n"
                else:
                    result += f"❌ Не найдено в реестре ФНС\n"
    except:
        result += f"⚠️ Не удалось запросить ФНС\n"

    result += f"\n🔗 *Проверь вручную:*\n"
    result += f"• [ЕГРЮЛ/ЕГРИП ФНС](https://egrul.nalog.ru/#) — официальный реестр\n"
    result += f"• [Контур.Фокус](https://focus.kontur.ru/search?query={inn_clean}) — полное досье\n"
    result += f"• [Rusprofile](https://www.rusprofile.ru/search?query={inn_clean}) — бесплатно"
    return result

async def fn_image(url: str) -> str:
    return (
        f"🖼 *Reverse Image Search*\n\n"
        f"🔗 Открой для поиска:\n\n"
        f"• [Яндекс Картинки](https://yandex.ru/images/search?url={url}&rpt=imageview) — лучший для RU\n"
        f"• [Google Lens](https://lens.google.com/uploadbyurl?url={url}) — Google\n"
        f"• [TinEye](https://tineye.com/search?url={url}) — точный поиск копий\n"
        f"• [Bing Visual](https://www.bing.com/images/search?q=imgurl:{url}&view=detailv2) — Microsoft\n\n"
        f"💡 Для лучшего результата загрузи фото напрямую на Яндекс Картинки!"
    )

# ─── Генерация красивого PDF ─────────────────────────────────────────────────

from fpdf import FPDF
import tempfile
import os
from datetime import datetime

class OSINTPDF(FPDF):
    def header(self):
        self.set_font("DejaVu", "B", 15)
        self.set_text_color(25, 55, 140)
        self.cell(0, 10, "🕵️ OSINT Досье", ln=True, align="C")
        self.set_font("DejaVu", "", 11)
        self.set_text_color(80, 80, 80)
        self.cell(0, 6, f"Запрос: {getattr(self, 'query', '')}", ln=True, align="C")
        self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.set_font("DejaVu", "", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Сформировано: {datetime.now().strftime('%d.%m.%Y %H:%M')} • Действительно 24 часа", align="C")

    def chapter_title(self, title):
        self.set_font("DejaVu", "B", 13)
        self.set_text_color(25, 55, 140)
        self.cell(0, 10, title, ln=True)
        self.ln(2)

    def chapter_body(self, text: str):
        self.set_font("DejaVu", "", 11)
        self.set_text_color(40, 40, 40)
        self.multi_cell(0, 6.5, text)
        self.ln(3)


def ensure_fonts():
    """Проверка шрифта для русского языка"""
    font_path = "DejaVuSans.ttf"
    if not os.path.exists(font_path):
        print("⚠️ DejaVuSans.ttf не найден. PDF будет с базовым шрифтом.")
    return True


def clean_text(text: str) -> str:
    """Очистка текста для PDF"""
    if not text:
        return ""
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)  # убираем markdown ссылки
    text = text.replace("✅", "✓").replace("❌", "✗").replace("⚠️", "!")
    return text.strip()


def generate_osint_pdf(query: str, input_type: str, sections: list, photos: list = None, expires_at=None) -> bytes:
    ensure_fonts()
    pdf = OSINTPDF()
    pdf.query = query
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # Шапка
    pdf.set_font("DejaVu", "B", 18)
    pdf.set_text_color(25, 55, 140)
    pdf.cell(0, 12, "ПОЛНОЕ ОСИНТ ДОСЬЕ", ln=True, align="C")

    pdf.set_font("DejaVu", "", 12)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(0, 8, f"Запрос: {query}", ln=True, align="C")
    pdf.cell(0, 8, f"Тип: {'📱 Телефон' if input_type == 'phone' else '📧 Email' if input_type == 'email' else '🔍 Никнейм'}", ln=True, align="C")
    
    if expires_at:
        pdf.cell(0, 8, f"Действительно до: {expires_at.strftime('%d.%m.%Y %H:%M')}", ln=True, align="C")
    pdf.ln(10)

    # Основные секции
    for section in sections:
        pdf.chapter_title(section.get("title", "Информация"))
        for line in section.get("lines", []):
            cleaned = clean_text(line)
            if cleaned:
                if len(cleaned) > 300:  # длинные строки
                    pdf.chapter_body(cleaned[:280] + "...")
                else:
                    pdf.chapter_body(cleaned)
        pdf.ln(4)

    # Фото профилей
    if photos and len(photos) > 0:
        pdf.add_page()
        pdf.chapter_title("📸 Фото профилей")
        x_start = 15
        y = pdf.get_y()
        for i, (platform, img_bytes) in enumerate(photos[:6]):
            try:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    tmp.write(img_bytes)
                    tmp_path = tmp.name
                
                pdf.image(tmp_path, x=x_start + (i % 2) * 95, y=y, w=85, h=85)
                os.unlink(tmp_path)
                
                pdf.set_xy(x_start + (i % 2) * 95, y + 88)
                pdf.set_font("DejaVu", "B", 9)
                pdf.cell(85, 6, platform, align="C")
                
                if i % 2 == 1:
                    y += 105
                    if y > 220:
                        pdf.add_page()
                        y = 20
            except:
                continue

    pdf_bytes = pdf.output(dest='S').encode('latin1')
    return pdf_bytes

def generate_osint_pdf(
    query: str,
    input_type: str,
    sections: list[dict],
    photos: list[tuple[str, bytes]],
    expires_at: datetime,
) -> bytes:
    """
    Генерирует PDF-досье и возвращает байты.
    Простой надёжный макет без сложного позиционирования.
    """
    from fpdf import FPDF

    TYPE_LABELS = {"phone": "Телефон", "email": "Email", "username": "Ник / Username"}
    exp_str    = expires_at.strftime("%d.%m.%Y %H:%M")
    now_str    = datetime.now().strftime("%d.%m.%Y %H:%M")
    W          = 180   # ширина контентной зоны (A4=210 - 2*15 отступов)

    class PDF(FPDF):
        def header(self):
            self.set_left_margin(15)
            self.set_right_margin(15)
            self.set_font("DejaVu", size=8)
            self.set_text_color(140, 140, 140)
            self.cell(W, 6, "OSINT Bot — автоматизированный отчёт. Только для законного использования.", align="C", new_x="LMARGIN", new_y="NEXT")
            self.set_draw_color(180, 180, 180)
            self.line(15, self.get_y(), 195, self.get_y())
            self.ln(3)

        def footer(self):
            self.set_y(-14)
            self.set_left_margin(15)
            self.set_right_margin(15)
            self.set_font("DejaVu", size=8)
            self.set_text_color(140, 140, 140)
            self.cell(W, 8, f"стр. {self.page_no()}   |   Действителен до: {exp_str}   |   Создан: {now_str}", align="C")

    pdf = PDF()
    pdf.add_font("DejaVu",          fname=FONT_PATH)
    pdf.add_font("DejaVu", style="B", fname=FONT_BOLD_PATH)
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)
    pdf.set_top_margin(15)
    pdf.set_auto_page_break(auto=True, margin=18)

    # ── Титул ────────────────────────────────────────────────────────────────
    pdf.add_page()

    pdf.set_font("DejaVu", style="B", size=24)
    pdf.set_text_color(15, 15, 50)
    pdf.cell(W, 14, "OSINT ДОСЬЕ", align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_draw_color(15, 15, 50)
    pdf.set_line_width(0.8)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.set_line_width(0.2)
    pdf.ln(5)

    pdf.set_font("DejaVu", size=12)
    pdf.set_text_color(60, 60, 60)
    pdf.cell(W, 8, "Разведка по открытым источникам", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)

    info_lines = [
        f"Запрос:      {_clean(query)}",
        f"Тип данных:  {TYPE_LABELS.get(input_type, input_type)}",
        f"Создан:      {now_str}",
        f"Действителен до: {exp_str}",
    ]
    pdf.set_font("DejaVu", size=11)
    pdf.set_text_color(30, 30, 30)
    for line in info_lines:
        pdf.cell(W, 7, line, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)
    pdf.set_font("DejaVu", size=9)
    pdf.set_text_color(160, 0, 0)
    pdf.cell(W, 6, "ВНИМАНИЕ: файл действителен 24 часа и будет удалён автоматически.", align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(8)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(4)
    pdf.set_font("DejaVu", size=8)
    pdf.set_text_color(140, 140, 140)
    pdf.multi_cell(W, 5,
        "Данный отчёт создан автоматически на основе открытых источников данных. "
        "Использование в незаконных целях запрещено. "
        "Точность данных не гарантируется — проверяйте информацию в первоисточниках.",
        align="C"
    )

    # ── Фотографии ───────────────────────────────────────────────────────────
    if photos:
        pdf.add_page()
        pdf.set_font("DejaVu", style="B", size=14)
        pdf.set_text_color(15, 15, 50)
        pdf.cell(W, 10, "Найденные фотографии профилей", new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(15, 15, 50)
        pdf.set_line_width(0.5)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.set_line_width(0.2)
        pdf.ln(5)

        col, row = 0, 0
        img_w, img_h, gap = 80, 75, 8
        x_base = [15, 110]

        for platform_name, img_bytes in photos:
            try:
                header4 = img_bytes[:4]
                ext = "PNG" if header4[:3] == b'\x89PN' else "JPEG"
                with tempfile.NamedTemporaryFile(suffix=f".{ext.lower()}", delete=False) as tmp:
                    tmp.write(img_bytes)
                    tmp_path = tmp.name

                x = x_base[col]
                y = 50 + row * (img_h + gap + 8)

                pdf.image(tmp_path, x=x, y=y, w=img_w, h=img_h)
                pdf.set_xy(x, y + img_h + 1)
                pdf.set_font("DejaVu", style="B", size=9)
                pdf.set_text_color(15, 15, 50)
                pdf.cell(img_w, 6, _clean(platform_name), align="C")

                os.unlink(tmp_path)
                col += 1
                if col >= 2:
                    col = 0
                    row += 1
            except Exception:
                pass

    # ── Разделы с данными ────────────────────────────────────────────────────
    for section in sections:
        pdf.add_page()
        pdf.set_left_margin(15)
        pdf.set_right_margin(15)

        # Заголовок
        pdf.set_font("DejaVu", style="B", size=13)
        pdf.set_text_color(15, 15, 50)
        title = _clean(section.get("title", ""))
        pdf.cell(W, 10, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(15, 15, 50)
        pdf.set_line_width(0.5)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.set_line_width(0.2)
        pdf.ln(2)

        # Источник
        pdf.set_font("DejaVu", size=8)
        pdf.set_text_color(120, 120, 120)
        source = _clean(section.get("source", "Открытые источники"))
        pdf.cell(W, 5, f"Источник: {source}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(210, 210, 210)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(4)

        # Строки содержимого
        pdf.set_font("DejaVu", size=10)
        pdf.set_text_color(30, 30, 30)
        for raw_line in section.get("lines", []):
            line = _clean(raw_line)
            if not line:
                pdf.ln(1)
                continue
            # Длинные URL — пропускаем
            if line.startswith("http") and len(line) > 100:
                continue
            try:
                pdf.set_x(15)
                pdf.multi_cell(W, 5.5, line, align="L")
            except Exception:
                pass

    return bytes(pdf.output())

# ─── Главный хендлер ─────────────────────────────────────────────────────────
dp = Dispatcher(storage=MemoryStorage())

WELCOME = (
    "🕵️ *OSINT Бот — Поисковая система*\n\n"
    "Ищу открытую информацию по различным данным.\n\n"
    "📌 *Примеры для ввода команд:*\n\n"
    "🔍 *Ник:* durov, elonmusk\n"
    "📱 *Телефон:* +79161234567\n"
    "📧 *Email:* example@mail.ru\n"
    "👤 *ВКонтакте:* durov или Иван Петров\n"
    "💬 *Telegram:* @durov\n"
    "🚗 *Авто:* А123БВ777\n"
    "🌐 *IP/Домен:* 8.8.8.8 или google.com\n"
    "🏢 *ИНН/ОГРН:* 7707083893\n"
    "🖼 *Фото:* отправь изображение\n\n"
    "Выбери инструмент 👇"
)

@dp.message(CommandStart())
async def cmd_start(msg: Message):
    upsert_user(msg.from_user.id, msg.from_user.username or "")
    rl = get_requests_left(msg.from_user.id)
    await msg.answer(WELCOME, parse_mode="Markdown", reply_markup=kb_main(msg.from_user.id, rl))

@dp.callback_query(F.data == "back_main")
async def cb_back(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    rl = get_requests_left(cb.from_user.id)
    await cb.message.edit_text(WELCOME, parse_mode="Markdown", reply_markup=kb_main(cb.from_user.id, rl))
    await cb.answer()

# ─── Меню покупки ────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "buy_menu")
async def cb_buy_menu(cb: CallbackQuery):
    user = get_user(cb.from_user.id)
    rl = get_requests_left(cb.from_user.id)
    bal = "∞" if is_admin(cb.from_user.id) else str(rl)
    bought = user["total_purchased"] if user else 0
    await cb.message.edit_text(
        f"💎 *Купить запросы*\n\n"
        f"💰 Баланс: *{bal} запросов*\n"
        f"📦 Куплено всего: *{bought}*\n\n"
        f"Запросы *не сгорают* — используй когда удобно!\n\n"
        f"20 запросов — 50 ⭐ (~60₽)\n"
        f"50 запросов — 100 ⭐ (~120₽)\n"
        f"100 запросов — 180 ⭐ (~220₽)\n"
        f"250 запросов — 350 ⭐ (~430₽)",
        parse_mode="Markdown",
        reply_markup=kb_buy()
    )
    await cb.answer()

@dp.callback_query(F.data.startswith("buy_pack_"))
async def cb_buy_pack(cb: CallbackQuery, bot: Bot):
    pack_key = cb.data.replace("buy_", "")
    pack = PACKAGES.get(pack_key)
    if not pack:
        await cb.answer("❌ Пакет не найден", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=f"OSINT — {pack['label']}",
        description=f"{pack['requests']} запросов. Не сгорают!",
        payload=f"osint_{pack_key}",
        currency="XTR",
        prices=[LabeledPrice(label=pack["label"], amount=pack["stars"])],
    )
    await cb.answer()

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, bot: Bot):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def payment_success(msg: Message):
    pack_key = msg.successful_payment.invoice_payload.replace("osint_", "")
    pack = PACKAGES.get(pack_key)
    if pack:
        add_requests(msg.from_user.id, pack["requests"])
        rl = get_requests_left(msg.from_user.id)
        await msg.answer(
            f"✅ *Оплата прошла!*\n\n"
            f"Добавлено: *+{pack['requests']} запросов*\n"
            f"Баланс: *{rl} запросов*\n\n"
            f"Удачи в поиске! 🕵️",
            parse_mode="Markdown",
            reply_markup=kb_main(msg.from_user.id, rl)
        )

# ─── Хелпер: проверка доступа ────────────────────────────────────────────────
async def check_access(cb: CallbackQuery, state: FSMContext, next_state: State, prompt: str):
    if not can_search(cb.from_user.id):
        await cb.message.edit_text("⛔ Запросы закончились!\n\nКупи ещё 👇", reply_markup=kb_buy())
        await cb.answer()
        return False
    await state.set_state(next_state)
    await cb.message.edit_text(prompt, parse_mode="Markdown", reply_markup=kb_back())
    await cb.answer()
    return True

async def send_result(msg: Message, result: str):
    rl = get_requests_left(msg.from_user.id)
    bal = "∞" if is_admin(msg.from_user.id) else str(rl)
    await msg.answer(result, parse_mode="Markdown", disable_web_page_preview=True)
    await msg.answer(f"💰 Баланс: *{bal} запросов*", parse_mode="Markdown",
                     reply_markup=kb_main(msg.from_user.id, rl))

# ─── Поиск по нику ───────────────────────────────────────────────────────────
@dp.callback_query(F.data == "s_username")
async def cb_username(cb: CallbackQuery, state: FSMContext):
    await check_access(cb, state, S.username,
        "🔍 *Поиск по нику*\n\nВведи username:\n_Пример: durov, elonmusk_")

@dp.message(S.username)
async def h_username(msg: Message, state: FSMContext):
    await state.clear()
    q = msg.text.strip().lstrip('@')
    use_request(msg.from_user.id)
    log_search(msg.from_user.id, "username", q)
    w = await msg.answer(f"🔍 Ищу *{q}* на 20 сайтах... ⏳", parse_mode="Markdown")
    result = await fn_username(q)
    await w.delete()
    await send_result(msg, result)

# ─── Телефон ─────────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "s_phone")
async def cb_phone(cb: CallbackQuery, state: FSMContext):
    await check_access(cb, state, S.phone,
        "📱 *Анализ номера телефона*\n\nВведи номер:\n_Пример: +79161234567 или 89161234567_")

@dp.message(S.phone)
async def h_phone(msg: Message, state: FSMContext):
    await state.clear()
    use_request(msg.from_user.id)
    log_search(msg.from_user.id, "phone", msg.text.strip())
    result = await fn_phone(msg.text.strip())
    await send_result(msg, result)

# ─── Email ───────────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "s_email")
async def cb_email(cb: CallbackQuery, state: FSMContext):
    await check_access(cb, state, S.email,
        "📧 *Проверка email*\n\nВведи email:\n_Пример: example@gmail.com_")

@dp.message(S.email)
async def h_email(msg: Message, state: FSMContext):
    await state.clear()
    use_request(msg.from_user.id)
    log_search(msg.from_user.id, "email", msg.text.strip())
    result = await fn_email(msg.text.strip().lower())
    await send_result(msg, result)

# ─── ВКонтакте ───────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "s_vk")
async def cb_vk(cb: CallbackQuery, state: FSMContext):
    await check_access(cb, state, S.vk,
        "👤 *Поиск ВКонтакте*\n\nВведи имя, фамилию или username:\n_Пример: durov или Иван Петров_")

@dp.message(S.vk)
async def h_vk(msg: Message, state: FSMContext):
    await state.clear()
    use_request(msg.from_user.id)
    log_search(msg.from_user.id, "vk", msg.text.strip())
    w = await msg.answer("👤 Ищу ВКонтакте... ⏳")
    result = await fn_vk(msg.text.strip())
    await w.delete()
    await send_result(msg, result)

# ─── Telegram ────────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "s_telegram")
async def cb_telegram(cb: CallbackQuery, state: FSMContext):
    await check_access(cb, state, S.telegram,
        "💬 *Поиск Telegram*\n\nВведи username:\n_Пример: @durov или durov_")

@dp.message(S.telegram)
async def h_telegram(msg: Message, state: FSMContext):
    await state.clear()
    use_request(msg.from_user.id)
    log_search(msg.from_user.id, "telegram", msg.text.strip())
    w = await msg.answer("💬 Проверяю Telegram... ⏳")
    result = await fn_telegram(msg.text.strip())
    await w.delete()
    await send_result(msg, result)

# ─── Авто ────────────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "s_car")
async def cb_car(cb: CallbackQuery, state: FSMContext):
    await check_access(cb, state, S.car,
        "🚗 *Проверка авто по номеру*\n\nВведи госномер:\n_Пример: А123БВ777 или A123BV77_")

@dp.message(S.car)
async def h_car(msg: Message, state: FSMContext):
    await state.clear()
    use_request(msg.from_user.id)
    log_search(msg.from_user.id, "car", msg.text.strip())
    w = await msg.answer("🚗 Проверяю номер... ⏳")
    result = await fn_car(msg.text.strip())
    await w.delete()
    await send_result(msg, result)

# ─── IP / Домен ──────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "s_ip")
async def cb_ip(cb: CallbackQuery, state: FSMContext):
    await check_access(cb, state, S.ip,
        "🌐 *Анализ IP / Домена*\n\nВведи IP-адрес или домен:\n_Примеры: 8.8.8.8 или google.com_")

@dp.message(S.ip)
async def h_ip(msg: Message, state: FSMContext):
    await state.clear()
    use_request(msg.from_user.id)
    log_search(msg.from_user.id, "ip", msg.text.strip())
    w = await msg.answer("🌐 Анализирую... ⏳")
    result = await fn_ip(msg.text.strip())
    await w.delete()
    await send_result(msg, result)

# ─── ИНН / ОГРН ──────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "s_inn")
async def cb_inn(cb: CallbackQuery, state: FSMContext):
    await check_access(cb, state, S.inn,
        "🏢 *Проверка ИНН / ОГРН*\n\nВведи ИНН или ОГРН компании:\n_Пример: 7707083893_")

@dp.message(S.inn)
async def h_inn(msg: Message, state: FSMContext):
    await state.clear()
    use_request(msg.from_user.id)
    log_search(msg.from_user.id, "inn", msg.text.strip())
    w = await msg.answer("🏢 Запрашиваю ФНС... ⏳")
    result = await fn_inn(msg.text.strip())
    await w.delete()
    await send_result(msg, result)

# ─── Reverse Image ────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "s_image")
async def cb_image(cb: CallbackQuery, state: FSMContext):
    await check_access(cb, state, S.image,
        "🖼 *Reverse Image Search*\n\nОтправь фото или ссылку на изображение:\n_Пример: https://example.com/photo.jpg_")

@dp.message(S.image)
async def h_image(msg: Message, state: FSMContext, bot: Bot):
    await state.clear()
    use_request(msg.from_user.id)
    if msg.photo:
        file = await bot.get_file(msg.photo[-1].file_id)
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        log_search(msg.from_user.id, "image", "photo")
    elif msg.text and msg.text.startswith("http"):
        url = msg.text.strip()
        log_search(msg.from_user.id, "image", url)
    else:
        await msg.answer("❌ Отправь ссылку на фото или само фото", reply_markup=kb_back())
        return
    result = await fn_image(url)
    await send_result(msg, result)

# ─── Админ-панель ────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "admin_panel")
async def cb_admin(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Нет доступа", show_alert=True)
        return
    s = get_stats()
    top = "\n".join([f"  • {t[0]}: {t[1]} раз" for t in s["top"]]) or "  Нет данных"
    await cb.message.edit_text(
        f"👑 *Админ-панель*\n\n"
        f"👥 Пользователей: *{s['total']}*\n"
        f"💰 Платящих: *{s['paid']}*\n"
        f"🔍 Поисков: *{s['searches']}*\n\n"
        f"📊 Топ запросов:\n{top}\n\n"
        f"Команды:\n"
        f"`/give USER_ID количество`",
        parse_mode="Markdown",
        reply_markup=kb_admin()
    )
    await cb.answer()

@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Нет доступа", show_alert=True)
        return
    s = get_stats()
    await cb.answer(f"👥 {s['total']} | 💰 {s['paid']} платящих | 🔍 {s['searches']} поисков", show_alert=True)

@dp.callback_query(F.data == "admin_give")
async def cb_admin_give(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Нет доступа", show_alert=True)
        return
    await cb.message.edit_text(
        "🎁 *Выдать запросы*\n\n`/give USER_ID количество`\nПример: `/give 123456789 50`",
        parse_mode="Markdown", reply_markup=kb_admin()
    )
    await cb.answer()

@dp.message(Command("give"))
async def cmd_give(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    args = msg.text.split()
    if len(args) != 3:
        await msg.answer("Использование: /give [user_id] [количество]")
        return
    try:
        target_id, count = int(args[1]), int(args[2])
    except ValueError:
        await msg.answer("❌ Неверный формат")
        return
    user = get_user(target_id)
    if not user:
        await msg.answer(f"❌ Пользователь {target_id} не найден")
        return
    add_requests(target_id, count)
    user = get_user(target_id)
    await msg.answer(f"✅ {target_id} получил {count} запросов. Баланс: {user['requests_left']}")
    try:
        await msg.bot.send_message(target_id, f"🎁 Тебе добавлено {count} запросов! Баланс: {user['requests_left']}")
    except:
        pass

# ─── Полное досье ────────────────────────────────────────────────────────────
def detect_input_type(query: str) -> str:
    """Определяет тип входных данных: phone / email / username"""
    q = query.strip()
    if re.match(r'^[\+\d][\d\s\-\(\)]{6,}$', q):
        return "phone"
    if re.match(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$', q):
        return "email"
    return "username"

async def fn_full_search(query: str) -> tuple[list[str], list[dict], str]:
    """
    Запускает все релевантные поиски по одному идентификатору.
    Возвращает (telegram_parts, pdf_sections, input_type).
    """
    q = query.strip()
    input_type = detect_input_type(q)

    tg_parts: list[str] = []
    pdf_sections: list[dict] = []

    header = (
        f"🗂 *ПОЛНОЕ ДОСЬЕ*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔎 Запрос: `{q}`\n"
        f"📌 Тип: *{'📱 Телефон' if input_type == 'phone' else '📧 Email' if input_type == 'email' else '🔍 Username/Ник'}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )
    tg_parts.append(header)

    if input_type == "phone":
        clean = re.sub(r'[^\d+]', '', q)
        num_digits = re.sub(r'[^\d]', '', clean)

        phone_text = await fn_phone(q)
        tg_parts.append(phone_text)
        pdf_sections.append({
            "title": "Анализ телефонного номера",
            "source": "Локальная база операторов РФ + открытые сервисы",
            "lines": phone_text.split("\n"),
        })

        tg_text = (
            f"💬 *Telegram по номеру:*\n"
            f"• [Открыть в Telegram](https://t.me/+{num_digits})\n"
            f"• [TGStat поиск](https://tgstat.ru/search?q={num_digits})\n"
        )
        tg_parts.append(tg_text)
        pdf_sections.append({
            "title": "Telegram по номеру",
            "source": "t.me / tgstat.ru",
            "lines": [
                f"Ссылка для открытия: https://t.me/+{num_digits}",
                f"Поиск TGStat: https://tgstat.ru/search?q={num_digits}",
            ],
        })

        vk_text = await fn_vk(num_digits)
        tg_parts.append(f"*ВКонтакте по номеру:*\n" + vk_text)
        pdf_sections.append({
            "title": "ВКонтакте — поиск по номеру",
            "source": "vk.com",
            "lines": vk_text.split("\n"),
        })

    elif input_type == "email":
        nick = q.split('@')[0]

        email_text = await fn_email(q)
        tg_parts.append(email_text)
        pdf_sections.append({
            "title": "Анализ Email",
            "source": "HaveIBeenPwned / DeHashed / BreachDirectory",
            "lines": email_text.split("\n"),
        })

        tg_parts.append(f"🔍 *Поиск по нику из email (`{nick}`):*")
        username_text = await fn_username(nick)
        tg_parts.append(username_text)
        pdf_sections.append({
            "title": f"Поиск ника из email: {nick}",
            "source": "20 социальных платформ (Sherlock-подход)",
            "lines": username_text.split("\n"),
        })

        vk_text = await fn_vk(nick)
        tg_parts.append(vk_text)
        pdf_sections.append({
            "title": "ВКонтакте по нику",
            "source": "vk.com",
            "lines": vk_text.split("\n"),
        })

        tg_text = await fn_telegram(nick)
        tg_parts.append(tg_text)
        pdf_sections.append({
            "title": "Telegram по нику",
            "source": "t.me",
            "lines": tg_text.split("\n"),
        })

    else:
        nick = q.lstrip('@')

        username_text = await fn_username(nick)
        tg_parts.append(username_text)
        pdf_sections.append({
            "title": "Поиск по нику — 20 платформ",
            "source": "ВКонтакте, Instagram, Twitter, TikTok, GitHub, YouTube, Telegram, Reddit и др.",
            "lines": username_text.split("\n"),
        })

        vk_text = await fn_vk(nick)
        tg_parts.append(vk_text)
        pdf_sections.append({
            "title": "ВКонтакте",
            "source": "vk.com",
            "lines": vk_text.split("\n"),
        })

        tg_text = await fn_telegram(nick)
        tg_parts.append(tg_text)
        pdf_sections.append({
            "title": "Telegram",
            "source": "t.me",
            "lines": tg_text.split("\n"),
        })

    tg_parts.append(
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ *Досье сформировано*\n"
        f"📄 Генерирую PDF-отчёт... ⏳"
    )
    return tg_parts, pdf_sections, input_type


async def _delete_pdf_later(bot: Bot, chat_id: int, message_id: int):
    """Удаляет PDF через 24 часа."""
    await asyncio.sleep(86400)
    try:
        await bot.delete_message(chat_id, message_id)
        await bot.send_message(
            chat_id,
            "🗑 *PDF-досье удалено*\n"
            "_Срок действия 24 часа истёк. Повторите запрос при необходимости._",
            parse_mode="Markdown"
        )
    except Exception:
        pass


@dp.callback_query(F.data == "s_full")
async def cb_full(cb: CallbackQuery, state: FSMContext):
    await check_access(cb, state, S.full_search,
        "🗂 *Полное досье — всё сразу*\n\n"
        "Введи один из:\n"
        "📱 *Телефон:* `+79161234567`\n"
        "📧 *Email:* `example@mail.ru`\n"
        "🔍 *Ник:* `durov`\n\n"
        "Бот сам определит тип и соберёт всё возможное, "
        "включая фото профилей и PDF-отчёт 🕵️")

@dp.message(S.full_search)
async def h_full(msg: Message, state: FSMContext, bot: Bot):
    await state.clear()
    q = msg.text.strip()
    use_request(msg.from_user.id)
    log_search(msg.from_user.id, "full_search", q)

    w = await msg.answer(
        "🗂 Собираю полное досье...\n\n"
        "⏳ Шаг 1/3: поиск по источникам",
        parse_mode="Markdown"
    )

    try:
        tg_parts, pdf_sections, input_type = await fn_full_search(q)

        # Шаг 1 — отправляем текстовые части
        await w.edit_text("⏳ Шаг 2/3: ищу фотографии профилей...", parse_mode="Markdown")

        # Определяем ник для поиска фото
        if input_type == "phone":
            nick_for_photos = None
        elif input_type == "email":
            nick_for_photos = q.split('@')[0]
        else:
            nick_for_photos = q.strip().lstrip('@')

        # Шаг 2 — ищем фото
        photos: list[tuple[str, bytes]] = []
        if nick_for_photos:
            photos = await fetch_profile_photos(nick_for_photos)

        # Шаг 3 — генерируем PDF
        await w.edit_text("⏳ Шаг 3/3: генерирую PDF-отчёт...", parse_mode="Markdown")
        await ensure_fonts()

        expires_at = datetime.now() + timedelta(hours=24)
        pdf_bytes = generate_osint_pdf(
            query=q,
            input_type=input_type,
            sections=pdf_sections,
            photos=photos,
            expires_at=expires_at,
        )

        await w.delete()

        # Отправляем текстовые части
        for part in tg_parts:
            if part and part.strip():
                try:
                    await msg.answer(part, parse_mode="Markdown", disable_web_page_preview=True)
                except Exception:
                    chunks = [part[i:i+3500] for i in range(0, len(part), 3500)]
                    for chunk in chunks:
                        await msg.answer(chunk, parse_mode="Markdown", disable_web_page_preview=True)

        # Отправляем фото (если нашли)
        if photos:
            await msg.answer(f"📸 *Найдено фотографий профилей: {len(photos)}*", parse_mode="Markdown")
            for platform_name, img_bytes in photos:
                try:
                    await msg.answer_photo(
                        BufferedInputFile(img_bytes, filename=f"{platform_name}.jpg"),
                        caption=f"📸 {platform_name}"
                    )
                except Exception:
                    pass

        # Отправляем PDF
        filename = f"osint_{q[:20].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
        pdf_msg = await msg.answer_document(
            BufferedInputFile(pdf_bytes, filename=filename),
            caption=(
                f"📄 *PDF-досье*\n"
                f"🔎 Запрос: `{q}`\n"
                f"⚠️ Действителен до: *{expires_at.strftime('%d.%m.%Y %H:%M')}*\n"
                f"_Файл будет автоматически удалён через 24 часа_"
            ),
            parse_mode="Markdown"
        )

        # Планируем удаление через 24 часа
        asyncio.create_task(_delete_pdf_later(bot, msg.chat.id, pdf_msg.message_id))

    except Exception as e:
        try:
            await w.delete()
        except Exception:
            pass
        await msg.answer(f"⚠️ Ошибка при сборе досье: {e}")
        log.exception("h_full error")

    rl = get_requests_left(msg.from_user.id)
    bal = "∞" if is_admin(msg.from_user.id) else str(rl)
    await msg.answer(
        f"💰 Баланс: *{bal} запросов*",
        parse_mode="Markdown",
        reply_markup=kb_main(msg.from_user.id, rl)
    )

# ─── Запуск ──────────────────────────────────────────────────────────────────
async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN)
    log.info("OSINT бот v2 запущен ✅")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
