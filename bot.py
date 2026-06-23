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

# ─── Фото из профилей ────────────────────────────────────────────────────────
async def _fetch_og_image(client: httpx.AsyncClient, url: str) -> bytes | None:
    """Пробует получить og:image с профильной страницы."""
    try:
        r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if r.status_code != 200:
            return None
        # og:image в обоих порядках атрибутов
        og = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](https?://[^"\']+)["\']', r.text
        ) or re.search(
            r'<meta[^>]+content=["\'](https?://[^"\']+)["\'][^>]+property=["\']og:image["\']', r.text
        )
        if og:
            img_r = await client.get(og.group(1), timeout=8)
            ct = img_r.headers.get("content-type", "")
            if img_r.status_code == 200 and "image" in ct:
                return img_r.content
    except Exception:
        pass
    return None

async def fetch_profile_photos(username: str) -> list[tuple[str, bytes]]:
    """
    Пытается скачать аватарки с популярных платформ.
    Возвращает список (платформа, bytes).
    """
    targets = [
        ("GitHub",    f"https://github.com/{username}"),
        ("Telegram",  f"https://t.me/{username}"),
        ("ВКонтакте", f"https://vk.com/{username}"),
        ("TikTok",    f"https://www.tiktok.com/@{username}"),
        ("Instagram", f"https://www.instagram.com/{username}/"),
        ("Twitter/X", f"https://twitter.com/{username}"),
    ]
    results: list[tuple[str, bytes]] = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for name, url in targets:
            data = await _fetch_og_image(client, url)
            if data:
                results.append((name, data))
            if len(results) >= 5:
                break
    return results

# ─── PDF генерация ────────────────────────────────────────────────────────────
FONT_PATH      = "/tmp/DejaVuSans.ttf"
FONT_BOLD_PATH = "/tmp/DejaVuSans-Bold.ttf"
FONT_URL       = "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans.ttf"
FONT_BOLD_URL  = "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans-Bold.ttf"

async def ensure_fonts():
    """Скачивает шрифт с поддержкой кириллицы, если ещё не скачан или повреждён."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for path, url in [(FONT_PATH, FONT_URL), (FONT_BOLD_PATH, FONT_BOLD_URL)]:
            # Скачиваем если нет или файл слишком мал (значит скачался не тот файл)
            if not os.path.exists(path) or os.path.getsize(path) < 50_000:
                log.info(f"Скачиваю шрифт: {url}")
                r = await client.get(url)
                r.raise_for_status()
                with open(path, "wb") as f:
                    f.write(r.content)
                log.info(f"Шрифт сохранён: {path} ({len(r.content)} байт)")

def _clean(text: str) -> str:
    """Убирает Markdown-символы и эмодзи для вставки в PDF (DejaVu не поддерживает emoji)."""
    # Markdown
    text = re.sub(r'[*_`]', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)  # [text](url) → text
    # Эмодзи и спецсимволы вне Latin/Cyrillic
    text = re.sub(
        r'[\U0001F000-\U0001FFFF'   # всякие эмодзи
        r'\U00002600-\U000027BF'    # разные символы (☀✂ и др.)
        r'\U0001F900-\U0001F9FF'    # дополнительные эмодзи
        r'\U00002702-\U000027B0'
        r'\U000024C2-\U0001F251'
        r'\U0001FA00-\U0001FA6F'
        r'\U0001FA70-\U0001FAFF'
        r'\u200d\ufe0f\u20e3'       # zero-width joiner, variation selector
        r']+',
        '', text, flags=re.UNICODE
    )
    # Убираем лишние пробелы
    text = re.sub(r'  +', ' ', text)
    return text.strip()

def generate_osint_pdf(
    query: str,
    input_type: str,
    sections: list[dict],
    photos: list[tuple[str, bytes]],
    expires_at: datetime,
) -> bytes:
    """
    PDF-досье в стиле Sherlock Report:
    - белый фон, чистая типографика
    - шапка с логотипом и аватаром
    - оглавление
    - секции с таблицами ключ-значение
    - теги для возможных имён
    """
    from fpdf import FPDF, XPos, YPos

    # Палитра (Sherlock-стиль: минималистичная, светлая)
    WHITE    = (255, 255, 255)
    BG       = (248, 249, 250)   # очень светло-серый фон строк
    DARK     = (25,  25,  25)    # основной текст
    MID      = (90,  90,  90)    # метки/ключи
    LIGHT_LINE = (220, 220, 220) # линии-разделители
    ACCENT   = (30,  30,  30)    # заголовки секций
    TAG_BG   = (240, 240, 240)   # фон тегов имён
    TAG_FG   = (50,  50,  50)    # текст тегов
    LOGO_COL = (20,  20,  20)    # OSINT (тёмный)
    SUB_COL  = (130, 130, 130)   # Report (серый)
    YEAR_COL = (160, 160, 160)   # год рядом с заголовком

    exp_str = expires_at.strftime("%d.%m.%Y %H:%M")
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    W   = 180   # контентная ширина при полях 15мм
    COL1 = 58   # ширина колонки-ключа
    COL2 = W - COL1

    TYPE_LABELS = {"phone": "Телефон", "email": "Email", "username": "Username / Ник"}

    # ── PDF класс с колонтитулами ─────────────────────────────────────────────
    class ReportPDF(FPDF):
        def header(self):
            self.set_left_margin(15)
            self.set_right_margin(15)
            # Логотип "OSINT Report"
            self.set_xy(15, 8)
            self.set_font("Bold", size=11)
            self.set_text_color(*LOGO_COL)
            self.cell(22, 7, "OSINT")
            self.set_font("Reg", size=11)
            self.set_text_color(*SUB_COL)
            self.cell(20, 7, " Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            # Горизонтальная линия под шапкой
            self.set_draw_color(*LIGHT_LINE)
            self.line(15, 17, 195, 17)
            self.ln(4)

        def footer(self):
            self.set_y(-13)
            self.set_draw_color(*LIGHT_LINE)
            self.line(15, self.get_y(), 195, self.get_y())
            self.set_font("Reg", size=7.5)
            self.set_text_color(*MID)
            self.set_left_margin(15)
            self.cell(W // 2, 7, f"Создан: {now_str}   |   Действителен до: {exp_str}")
            self.cell(W // 2, 7, f"стр. {self.page_no()}", align="R")

    pdf = ReportPDF()
    pdf.add_font("Reg",  fname=FONT_PATH)
    pdf.add_font("Bold", fname=FONT_BOLD_PATH)
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)
    pdf.set_top_margin(22)
    pdf.set_auto_page_break(auto=True, margin=16)

    # ── Вспомогательные функции ───────────────────────────────────────────────
    def sec_title(title: str, subtitle: str = ""):
        """Заголовок секции как в Sherlock: жирный + год/источник серым."""
        pdf.set_x(15)
        pdf.set_font("Bold", size=13)
        pdf.set_text_color(*ACCENT)
        tw = pdf.get_string_width(title)
        pdf.cell(tw + 2, 9, title)
        if subtitle:
            pdf.set_font("Reg", size=10)
            pdf.set_text_color(*YEAR_COL)
            pdf.cell(30, 9, f" {subtitle}")
        pdf.ln(9)
        pdf.set_draw_color(*LIGHT_LINE)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(4)

    def kv(key: str, value: str, shade: bool = False):
        """Строка ключ — значение."""
        if shade:
            pdf.set_fill_color(*BG)
        else:
            pdf.set_fill_color(*WHITE)
        pdf.set_x(15)
        pdf.set_font("Reg", size=9)
        pdf.set_text_color(*MID)
        pdf.cell(COL1, 7, key[:35], fill=True)
        pdf.set_font("Reg", size=9)
        pdf.set_text_color(*DARK)
        # Обрезаем значение если слишком длинное
        val = value[:80] if len(value) > 80 else value
        pdf.cell(COL2, 7, val, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def tbl_header(cols: list[tuple[str, float]]):
        """Шапка таблицы: серые метки."""
        pdf.set_x(15)
        pdf.set_font("Reg", size=8.5)
        pdf.set_text_color(*MID)
        pdf.set_draw_color(*LIGHT_LINE)
        for label, w in cols:
            pdf.cell(w, 6, label)
        pdf.ln(6)
        pdf.set_draw_color(*LIGHT_LINE)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(1)

    def tbl_row(cells: list[tuple[str, float]], shade: bool):
        pdf.set_x(15)
        pdf.set_font("Reg", size=9)
        pdf.set_text_color(*DARK)
        pdf.set_fill_color(*BG if shade else WHITE)
        for val, w in cells:
            pdf.cell(w, 6.5, str(val)[:55], fill=True)
        pdf.ln(6.5)

    def draw_tags(items: list[str]):
        """Рисует теги-чипы как в Sherlock (возможные имена)."""
        pdf.set_font("Reg", size=9)
        x, y = 15.0, pdf.get_y()
        line_h = 8.0
        gap_x  = 3.0

        for item in items:
            tw = pdf.get_string_width(item) + 6
            if x + tw > 193:
                x  = 15.0
                y += line_h + 2

            # Рамка тега
            pdf.set_draw_color(*LIGHT_LINE)
            pdf.set_fill_color(*TAG_BG)
            pdf.rect(x, y, tw, line_h - 1, style="FD")
            pdf.set_xy(x + 3, y + 1)
            pdf.set_text_color(*TAG_FG)
            pdf.cell(tw - 6, line_h - 3, item)
            x += tw + gap_x

        pdf.set_y(y + line_h + 2)

    # ════════════════════════════════════════════════════════════════════
    # Страница 1: шапка — аватар + основные данные
    # ════════════════════════════════════════════════════════════════════
    pdf.add_page()

    top_y = pdf.get_y()

    # Аватар (первое фото, если есть)
    avatar_placed = False
    if photos:
        try:
            plat, img_bytes = photos[0]
            ext = "PNG" if img_bytes[:3] == b'\x89PN' else "JPEG"
            with tempfile.NamedTemporaryFile(suffix=f".{ext.lower()}", delete=False) as tmp:
                tmp.write(img_bytes)
                tmp_path = tmp.name
            # Скруглённая рамка (имитация) — просто светло-серый квадрат
            pdf.set_fill_color(*BG)
            pdf.rect(15, top_y, 34, 34, style="F")
            pdf.image(tmp_path, x=16, y=top_y + 1, w=32, h=32)
            os.unlink(tmp_path)
            avatar_placed = True
        except Exception:
            pass

    # Основной запрос крупно
    text_x = 55 if avatar_placed else 15
    text_w = W - (40 if avatar_placed else 0)

    pdf.set_xy(text_x, top_y + 3)
    pdf.set_font("Bold", size=22)
    pdf.set_text_color(*DARK)
    pdf.cell(text_w, 12, _clean(query)[:40])
    pdf.set_xy(text_x, top_y + 16)
    pdf.set_font("Reg", size=10)
    pdf.set_text_color(*MID)
    lbl = TYPE_LABELS.get(input_type, input_type)
    pdf.cell(text_w, 7, lbl)
    pdf.ln(24)

    # Краткая сводка — первая секция
    sec_title("Краткая сводка")

    shade = False
    kv("Запрос",          _clean(query),                         shade); shade = not shade
    kv("Тип данных",      TYPE_LABELS.get(input_type, input_type), shade); shade = not shade
    kv("Дата отчёта",     now_str,                               shade); shade = not shade
    kv("Действителен до", exp_str,                               shade)
    pdf.ln(6)

    pdf.set_x(15)
    pdf.set_font("Reg", size=7.5)
    pdf.set_text_color(*MID)
    pdf.multi_cell(W, 4.5,
        "Данный отчёт создан автоматически на основе открытых источников. "
        "Точность не гарантируется. Использование в незаконных целях запрещено.",
        align="C")

    # ════════════════════════════════════════════════════════════════════
    # Страница 2+: фотографии профилей (если есть несколько)
    # ════════════════════════════════════════════════════════════════════
    if len(photos) > 1:
        pdf.add_page()
        sec_title("Фотографии профилей")

        col_x = [15.0, 105.0]
        col   = 0
        row_y = pdf.get_y()

        for platform_name, img_bytes in photos:
            try:
                ext = "PNG" if img_bytes[:3] == b'\x89PN' else "JPEG"
                with tempfile.NamedTemporaryFile(suffix=f".{ext.lower()}", delete=False) as tmp:
                    tmp.write(img_bytes)
                    tmp_path = tmp.name

                x = col_x[col]
                y = row_y

                # Светлый фон-плашка
                pdf.set_fill_color(*BG)
                pdf.rect(x, y, 82, 78, style="F")
                pdf.image(tmp_path, x=x + 1, y=y + 1, w=80, h=72)

                # Подпись
                pdf.set_xy(x, y + 74)
                pdf.set_font("Bold", size=8.5)
                pdf.set_text_color(*DARK)
                pdf.cell(82, 5, _clean(platform_name), align="C")

                os.unlink(tmp_path)
                col += 1
                if col >= 2:
                    col   = 0
                    row_y += 86
                    pdf.set_y(row_y)
            except Exception:
                pass

    # ════════════════════════════════════════════════════════════════════
    # Секции с данными
    # ════════════════════════════════════════════════════════════════════
    for sec in sections:
        pdf.add_page()

        raw_title  = _clean(sec.get("title",  ""))
        raw_source = _clean(sec.get("source", ""))
        lines      = sec.get("lines", [])

        # Разделяем заголовок и год если есть (формат "Заголовок 2024")
        year_match = re.search(r'\b(20\d{2})\b', raw_title)
        if year_match:
            year      = year_match.group(1)
            clean_ttl = raw_title[:year_match.start()].strip()
        else:
            year      = raw_source[:30] if raw_source else ""
            clean_ttl = raw_title

        sec_title(clean_ttl, year)

        # Парсим строки в пары ключ-значение
        pairs: list[tuple[str, str]] = []
        tags:  list[str]             = []
        other: list[str]             = []

        for raw in lines:
            cl = _clean(raw).strip().lstrip("- ")
            if not cl or (cl.startswith("http") and len(cl) > 80):
                continue

            stripped = cl.lstrip("• ")

            # Ключ: значение
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                k, v = k.strip(), v.strip()
                if k and v and len(k) < 40 and "\n" not in k:
                    pairs.append((k, v))
                    continue

            # Короткое слово/фраза без двоеточия — тег
            if len(stripped) < 30 and " " not in stripped[:10]:
                tags.append(stripped)
                continue

            other.append(cl)

        # Рендер пар как таблица
        if pairs:
            shade = False
            for k, v in pairs:
                kv(k, v, shade)
                shade = not shade
            pdf.ln(4)

        # Теги (возможные имена и т.п.)
        if tags:
            draw_tags(tags)
            pdf.ln(3)

        # Остальные строки
        if other:
            pdf.set_font("Reg", size=9.5)
            pdf.set_text_color(*DARK)
            for line in other:
                try:
                    pdf.set_x(15)
                    pdf.multi_cell(W, 5.5, line, align="L")
                except Exception:
                    pass

    return bytes(pdf.output())

# ─── Главный хендлер ─────────────────────────────────────────────────────────
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

# ── Вспомогательные функции для карточки ─────────────────────────────────────
def _phone_info(clean: str) -> tuple[str, str, str]:
    """Возвращает (оператор, регион, страна) по номеру."""
    if clean.startswith('+7') and len(clean) == 12:
        code = clean[2:5]
        op   = OPERATORS_RU.get(code, 'Неизвестный оператор')
        # Таблица регионов по первым 5 цифрам
        REGIONS = {
            "916": "Москва и МО", "917": "Москва и МО", "925": "Москва и МО",
            "926": "Москва и МО", "903": "Москва и МО", "905": "Москва и МО",
            "906": "Москва и МО", "909": "Москва и МО", "910": "Центр РФ",
            "911": "Северо-Запад", "912": "Урал", "913": "Сибирь",
            "914": "Дальний Восток", "918": "Юг России", "919": "Сибирь",
            "920": "Центр РФ", "921": "Северо-Запад", "922": "Урал",
            "923": "Сибирь", "924": "Дальний Восток", "927": "Поволжье",
            "928": "Юг России", "929": "Центр РФ", "930": "Центр РФ",
            "931": "Северо-Запад", "932": "Урал", "933": "Юг России",
            "934": "Поволжье", "936": "Центр РФ", "937": "Поволжье",
            "938": "Юг России", "939": "Поволжье", "950": "Урал",
            "951": "Урал", "952": "Урал", "953": "Сибирь",
            "958": "Москва и МО", "960": "Центр РФ", "961": "Юг России",
            "962": "Поволжье", "963": "Юг России", "964": "Дальний Восток",
            "965": "Москва и МО", "967": "Москва и МО", "968": "Москва и МО",
            "977": "Москва и МО", "980": "Центр РФ", "981": "Северо-Запад",
            "982": "Урал", "985": "Москва и МО", "987": "Поволжье",
            "988": "Юг России", "989": "Юг России", "993": "Москва и МО",
            "995": "Москва и МО", "996": "Урал", "999": "Москва и МО",
        }
        region = REGIONS.get(code, "Россия")
        return op, region, "Россия"
    if clean.startswith('+380'): return "—", "—", "Украина"
    if clean.startswith('+375'): return "—", "—", "Беларусь"
    if clean.startswith('+7'):   return "—", "—", "Россия / Казахстан"
    return "—", "—", "Международный"

async def _check_social_profiles(nick: str) -> list[tuple[str, str, str]]:
    """
    Проверяет публичные профили на платформах.
    Возвращает список (платформа, отображаемое имя, url).
    """
    TARGETS = [
        ("ВКонтакте",  f"https://vk.com/{nick}",              r'<title>([^<|—]+)'),
        ("Instagram",  f"https://www.instagram.com/{nick}/",   r'"full_name":"([^"]+)"'),
        ("TikTok",     f"https://www.tiktok.com/@{nick}",      r'"nickname":"([^"]+)"'),
        ("GitHub",     f"https://github.com/{nick}",           r'<title>([^·<]+)'),
        ("Twitter/X",  f"https://twitter.com/{nick}",          r'<title>([^(·<]+)'),
        ("YouTube",    f"https://www.youtube.com/@{nick}",     r'<title>([^-<·]+)'),
        ("Telegram",   f"https://t.me/{nick}",                 r'og:title" content="([^"]+)"'),
        ("Pinterest",  f"https://www.pinterest.com/{nick}/",   r'<title>([^(|<]+)'),
        ("Twitch",     f"https://www.twitch.tv/{nick}",        r'<title>([^-<·]+)'),
        ("Steam",      f"https://steamcommunity.com/id/{nick}",r'<title>([^-<·]+)'),
        ("Reddit",     f"https://www.reddit.com/user/{nick}/", r'<title>([^-<·]+)'),
    ]
    found = []
    async with httpx.AsyncClient(
        timeout=8, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; OSINT-Bot)"}
    ) as client:
        async def _check(platform, url, pattern):
            try:
                r = await client.get(url)
                if r.status_code == 200 and 'not found' not in r.text.lower()[:500]:
                    m = re.search(pattern, r.text, re.IGNORECASE)
                    name = m.group(1).strip() if m else nick
                    # Фильтруем мусор
                    if len(name) > 1 and name.lower() not in ('page not found', '404', 'error'):
                        found.append((platform, name[:40], url))
            except Exception:
                pass

        tasks = [_check(p, u, r) for p, u, r in TARGETS]
        await asyncio.gather(*tasks)
    return found

async def _check_telegram_by_phone(num_digits: str) -> dict:
    """Проверяет наличие Telegram аккаунта по номеру через t.me."""
    result = {"found": False, "name": None, "username": None}
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(f"https://t.me/+{num_digits}")
            if r.status_code == 200:
                text = r.text
                name_m = re.search(r'<meta property="og:title" content="([^"]+)"', text)
                user_m = re.search(r't\.me/([a-zA-Z0-9_]+)"', text)
                # Если не redirect на главную страницу TG — профиль существует
                if name_m and 'Telegram' not in name_m.group(1):
                    result["found"] = True
                    result["name"]  = name_m.group(1).strip()
                    if user_m:
                        result["username"] = user_m.group(1)
    except Exception:
        pass
    return result

async def _search_vk_by_phone(num_digits: str) -> list[dict]:
    """Ищет профили ВКонтакте по номеру телефона."""
    profiles = []
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(
                f"https://vk.com/search?c[section]=people&c[phone]={num_digits}"
            )
            if r.status_code == 200:
                # Ищем карточки пользователей
                ids   = re.findall(r'id=(\d+)', r.text)
                names = re.findall(r'<span class="labeled">([^<]+)</span>', r.text)
                for i, uid in enumerate(ids[:3]):
                    name = names[i] if i < len(names) else f"ID {uid}"
                    profiles.append({
                        "id":   uid,
                        "name": name,
                        "url":  f"https://vk.com/id{uid}",
                    })
    except Exception:
        pass
    return profiles

async def _search_avito_by_phone(num_digits: str) -> dict:
    """Проверяет объявления на Авито по номеру."""
    result = {"found": False, "count": 0, "url": ""}
    try:
        search_url = f"https://www.avito.ru/rossiya?q={num_digits}"
        async with httpx.AsyncClient(timeout=8, follow_redirects=True,
                                     headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(search_url)
            if r.status_code == 200:
                count_m = re.search(r'(\d+)\s+объявлени', r.text)
                if count_m and int(count_m.group(1)) > 0:
                    result["found"] = True
                    result["count"] = int(count_m.group(1))
                    result["url"]   = search_url
    except Exception:
        pass
    return result

async def fn_full_search(query: str) -> tuple[str, list[dict], str]:
    """
    Собирает полное досье и возвращает (card_text, pdf_sections, input_type).
    card_text — компактная карточка с реальными данными.
    """
    q          = query.strip()
    input_type = detect_input_type(q)
    pdf_sections: list[dict] = []

    phone_block    = ""
    live_block     = ""
    profiles_block = ""
    email_block    = ""
    nick_for_search = None

    if input_type == "phone":
        clean      = re.sub(r'[^\d+]', '', q)
        if not clean.startswith('+'): clean = '+7' + clean[1:] if clean.startswith('8') else '+' + clean
        num_digits = re.sub(r'[^\d]', '', clean)
        op, region, country = _phone_info(clean)

        phone_block = (
            f"📱 Телефон: `{clean}`\n"
            f"├ Оператор: {op}\n"
            f"├ Регион: {region}\n"
            f"└ Страна: {country}\n"
        )
        pdf_lines = [f"Телефон: {clean}", f"Оператор: {op}",
                     f"Регион: {region}", f"Страна: {country}"]

        # Параллельно проверяем реальные источники
        tg_res, vk_res, avito_res = await asyncio.gather(
            _check_telegram_by_phone(num_digits),
            _search_vk_by_phone(num_digits),
            _search_avito_by_phone(num_digits),
        )

        live_lines = []
        live_pdf   = []

        # Telegram
        if tg_res["found"]:
            name = tg_res["name"] or "—"
            user = f"@{tg_res['username']}" if tg_res["username"] else ""
            live_lines.append(f"💬 Telegram: [{name} {user}](https://t.me/+{num_digits})")
            live_pdf.append(f"Telegram: {name} {user} — https://t.me/+{num_digits}")
        else:
            live_lines.append(f"💬 Telegram: аккаунт не обнаружен / скрыт")
            live_pdf.append("Telegram: не найден")

        # ВКонтакте
        if vk_res:
            for p in vk_res:
                live_lines.append(f"👤 ВКонтакте: [{p['name']}]({p['url']})")
                live_pdf.append(f"ВКонтакте: {p['name']} — {p['url']}")
        else:
            live_lines.append("👤 ВКонтакте: профиль не найден (скрыт или не привязан)")
            live_pdf.append("ВКонтакте: не найден")

        # Авито
        if avito_res["found"]:
            live_lines.append(f"🛍 Авито: [{avito_res['count']} объявл.]({avito_res['url']})")
            live_pdf.append(f"Авито: {avito_res['count']} объявлений — {avito_res['url']}")
        else:
            live_lines.append("🛍 Авито: объявлений не найдено")
            live_pdf.append("Авито: не найдено")

        # Ссылки для ручной проверки (GetContact/NumBuster — только ссылки, требуют авторизацию)
        live_lines.append(
            f"\n🔗 *Проверить вручную:*\n"
            f"• [GetContact](https://getcontact.com/ru/{num_digits}) — имена из контактов\n"
            f"• [NumBuster](https://numbuster.com/number/{num_digits}) — телефонная книга"
        )

        live_block = "\n".join(live_lines)

        pdf_sections.append({"title": "Телефонный номер", "source": "База операторов РФ", "lines": pdf_lines})
        pdf_sections.append({"title": "Проверка по источникам", "source": "Telegram / ВКонтакте / Авито", "lines": live_pdf})

    elif input_type == "email":
        domain = q.split('@')[1]
        known  = {
            'gmail.com': 'Google Gmail', 'yahoo.com': 'Yahoo', 'mail.ru': 'Mail.ru',
            'yandex.ru': 'Яндекс', 'yandex.com': 'Яндекс', 'outlook.com': 'Microsoft',
            'icloud.com': 'Apple iCloud', 'rambler.ru': 'Rambler',
            'bk.ru': 'Mail.ru', 'list.ru': 'Mail.ru', 'inbox.ru': 'Mail.ru',
        }
        provider       = known.get(domain, domain)
        nick_for_search = q.split('@')[0]

        email_block = (
            f"📧 Email: `{q}`\n"
            f"├ Провайдер: {provider}\n"
            f"├ Утечки: [HaveIBeenPwned](https://haveibeenpwned.com/account/{q})\n"
            f"└ Ник из email: `{nick_for_search}`\n"
        )
        pdf_sections.append({
            "title": "Email", "source": "Анализ домена",
            "lines": [f"Email: {q}", f"Провайдер: {provider}", f"Ник: {nick_for_search}"],
        })

    else:
        nick_for_search = q.lstrip('@')

    # ── Поиск профилей по нику ────────────────────────────────────────────────
    social_profiles: list[tuple[str, str, str]] = []
    if nick_for_search:
        social_profiles = await _check_social_profiles(nick_for_search)
        if social_profiles:
            lines_pdf = []
            lines_tg  = []
            for platform, name, url in social_profiles:
                lines_tg.append(f"👤 {platform}: [{name}]({url})")
                lines_pdf.append(f"{platform}: {name} — {url}")
            profiles_block = "\n".join(lines_tg) + "\n"
            pdf_sections.append({
                "title": "Профили в интернете",
                "source": "Прямая проверка публичных URL",
                "lines": lines_pdf,
            })
        else:
            profiles_block = "❌ Публичных профилей не найдено\n"

    # ── Сборка карточки ───────────────────────────────────────────────────────
    card = f"⬤ *Обнаружен идентификатор:* `{q}`\n━━━━━━━━━━━━━━━━━━━━\n"

    if phone_block:
        card += f"\n{phone_block}"
    if email_block:
        card += f"\n{email_block}"
    if live_block:
        card += f"\n{live_block}\n"
    if profiles_block:
        n = len(social_profiles)
        card += f"\n🌐 *Профили ({n} найдено):*\n{profiles_block}"

    card += f"━━━━━━━━━━━━━━━━━━━━\n🕵️ *Генерирую PDF-отчёт...* ⏳"
    return card, pdf_sections, input_type



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

    w = await msg.answer("🔎 Ищу данные...", parse_mode="Markdown")

    try:
        # Шаг 1: собираем данные и карточку
        card_text, pdf_sections, input_type = await fn_full_search(q)

        # Шаг 2: ищем фото профилей
        await w.edit_text("📸 Ищу фото профилей...", parse_mode="Markdown")
        nick_for_photos = (
            None if input_type == "phone"
            else q.split('@')[0] if input_type == "email"
            else q.strip().lstrip('@')
        )
        photos: list[tuple[str, bytes]] = []
        if nick_for_photos:
            photos = await fetch_profile_photos(nick_for_photos)

        # Шаг 3: генерируем PDF
        await w.edit_text("📄 Генерирую PDF-отчёт...", parse_mode="Markdown")
        await ensure_fonts()
        expires_at = datetime.now() + timedelta(hours=24)
        pdf_bytes = generate_osint_pdf(
            query=q, input_type=input_type,
            sections=pdf_sections, photos=photos,
            expires_at=expires_at,
        )

        await w.delete()

        # Отправляем карточку (убираем строку про PDF из текста)
        clean_card = card_text.replace("📄 Генерирую PDF-отчёт... ⏳", "").rstrip()
        await msg.answer(clean_card, parse_mode="Markdown", disable_web_page_preview=True)

        # Фото профилей (если нашли)
        if photos:
            for platform_name, img_bytes in photos:
                try:
                    await msg.answer_photo(
                        BufferedInputFile(img_bytes, filename=f"{platform_name}.jpg"),
                        caption=f"📸 {platform_name}"
                    )
                except Exception:
                    pass

        # PDF
        filename = f"osint_{q[:20].replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.pdf"
        pdf_msg = await msg.answer_document(
            BufferedInputFile(pdf_bytes, filename=filename),
            caption=(
                f"📄 *PDF-досье* | `{q}`\n"
                f"⏳ Удалится: *{expires_at.strftime('%d.%m.%Y %H:%M')}*"
            ),
            parse_mode="Markdown"
        )
        asyncio.create_task(_delete_pdf_later(bot, msg.chat.id, pdf_msg.message_id))

    except Exception as e:
        try:
            await w.delete()
        except Exception:
            pass
        await msg.answer(f"⚠️ Ошибка: {e}")
        log.exception("h_full error")

    rl  = get_requests_left(msg.from_user.id)
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
