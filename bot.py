"""
OSINT Telegram Bot v2
Стек: Python 3.10+, aiogram 3, SQLite
"""

import asyncio
import logging
import sqlite3
import re
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery
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
    username = State()
    phone    = State()
    email    = State()
    image    = State()
    vk       = State()
    car      = State()
    ip       = State()
    inn      = State()
    telegram = State()

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

# ─── Запуск ──────────────────────────────────────────────────────────────────
async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN)
    log.info("OSINT бот v2 запущен ✅")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
        CREATE TABLE IF NOT EXISTS users (
            user_id         INTEGER PRIMARY KEY,
            username        TEXT,
            requests_left   INTEGER DEFAULT 3,
            total_purchased INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS searches (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER,
            type      TEXT,
            query     TEXT,
            created   TEXT
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
    return dict(zip(["user_id", "username", "requests_left", "total_purchased"], row))

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
    paid = con.execute("SELECT COUNT(*) FROM users WHERE total_purchased > 0").fetchone()[0]
    searches = con.execute("SELECT COUNT(*) FROM searches").fetchone()[0]
    top_types = con.execute(
        "SELECT type, COUNT(*) as cnt FROM searches GROUP BY type ORDER BY cnt DESC LIMIT 5"
    ).fetchall()
    con.close()
    return {"total": total, "paid": paid, "searches": searches, "top_types": top_types}

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

# ─── FSM ─────────────────────────────────────────────────────────────────────
class SearchStates(StatesGroup):
    waiting_username = State()
    waiting_phone    = State()
    waiting_email    = State()
    waiting_image    = State()
    waiting_vk       = State()

# ─── Клавиатуры ──────────────────────────────────────────────────────────────
def kb_main(user_id: int, requests_left: int) -> InlineKeyboardMarkup:
    bal = "∞" if is_admin(user_id) else str(requests_left)
    buttons = []
    if is_admin(user_id):
        buttons.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    buttons.extend([
        [
            InlineKeyboardButton(text="🔍 Поиск по нику", callback_data="search_username"),
            InlineKeyboardButton(text="📱 Номер телефона", callback_data="search_phone"),
        ],
        [
            InlineKeyboardButton(text="📧 Проверка email", callback_data="search_email"),
            InlineKeyboardButton(text="🖼 Reverse Image", callback_data="search_image"),
        ],
        [InlineKeyboardButton(text="👤 Поиск ВКонтакте", callback_data="search_vk")],
        [InlineKeyboardButton(text="💎 Купить запросы", callback_data="buy_requests")],
        [InlineKeyboardButton(text=f"📊 Баланс: {bal} запр.", callback_data="my_balance")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def kb_buy() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="20 запросов — 50 ⭐",  callback_data="buy_pack_20")],
        [InlineKeyboardButton(text="50 запросов — 100 ⭐", callback_data="buy_pack_50")],
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

# ─── OSINT функции ────────────────────────────────────────────────────────────
async def search_username(username: str) -> str:
    found = []
    errors = []
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        tasks = {
            name: client.get(url.format(username), headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            for name, url in SHERLOCK_SITES.items()
        }
        for site_name, task in tasks.items():
            url = SHERLOCK_SITES[site_name].format(username)
            try:
                response = await task
                if response.status_code == 200:
                    found.append(f"✅ [{site_name}]({url})")
                else:
                    errors.append(site_name)
            except:
                errors.append(site_name)

    result = f"🔍 Результаты поиска: `{username}`\n\n"
    if found:
        result += f"📍 Найдено на {len(found)} сайтах:\n" + "\n".join(found)
    else:
        result += "❌ Аккаунты не найдены"
    result += f"\n\n⬜ Недоступно/не найдено: {len(errors)} сайтов"
    return result

async def search_phone(phone: str) -> str:
    phone_clean = re.sub(r'[^\d+]', '', phone)
    if not phone_clean.startswith('+'):
        if phone_clean.startswith('8'):
            phone_clean = '+7' + phone_clean[1:]
        elif phone_clean.startswith('7'):
            phone_clean = '+' + phone_clean

    operators_ru = {
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

    result = f"📱 Анализ номера: `{phone_clean}`\n\n"
    if phone_clean.startswith('+7') and len(phone_clean) == 12:
        code = phone_clean[2:5]
        operator = operators_ru.get(code, 'Неизвестный оператор')
        result += f"🇷🇺 Страна: Россия\n📡 Оператор: {operator}\n🔢 Код: +7 ({code})\n\n"
    elif phone_clean.startswith('+380'):
        result += "🇺🇦 Страна: Украина\n\n"
    elif phone_clean.startswith('+375'):
        result += "🇧🇾 Страна: Беларусь\n\n"
    else:
        result += "🌍 Международный номер\n\n"

    result += (
        "🔗 Проверь вручную:\n"
        f"• [GetContact](https://getcontact.com) — имя в контактах\n"
        f"• [NumBuster](https://numbuster.com) — отзывы\n"
        f"• [Truecaller](https://www.truecaller.com/search/ru/{phone_clean.replace('+', '')}) — поиск имени"
    )
    return result

async def search_email(email: str) -> str:
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return "❌ Неверный формат email"

    domain = email.split('@')[1]
    known_domains = {
        'gmail.com':'Google Gmail', 'yahoo.com':'Yahoo Mail',
        'mail.ru':'Mail.ru', 'yandex.ru':'Яндекс Почта',
        'outlook.com':'Microsoft Outlook', 'hotmail.com':'Microsoft Hotmail',
        'icloud.com':'Apple iCloud', 'rambler.ru':'Rambler',
        'bk.ru':'Mail.ru (bk)', 'list.ru':'Mail.ru (list)', 'inbox.ru':'Mail.ru (inbox)',
    }
    provider = known_domains.get(domain, f'Неизвестный провайдер ({domain})')

    result = (
        f"📧 Анализ email: `{email}`\n\n"
        f"📮 Провайдер: {provider}\n\n"
        f"🔐 Проверка утечек:\n"
        f"• [HaveIBeenPwned](https://haveibeenpwned.com/account/{email}) — международные утечки\n"
        f"• [DeHashed](https://dehashed.com/search?query={email}) — базы данных\n"
        f"• [LeakCheck](https://leakcheck.io) — российские утечки\n\n"
        f"🔍 Поиск в соцсетях:\n"
        f"• [ВКонтакте](https://vk.com/search?c[section]=people&c[email]={email}) — поиск по email\n"
        f"• [Facebook](https://www.facebook.com/search/top?q={email}) — поиск"
    )
    return result

async def search_vk(query: str) -> str:
    """Поиск ВКонтакте через публичный веб без токена."""
    query_enc = query.strip().replace(' ', '+')
    is_username = ' ' not in query.strip() and not any(c.isspace() for c in query.strip())

    result = f"👤 Поиск ВКонтакте: `{query}`\n\n"

    # Пробуем получить профиль по username через открытую страницу
    if is_username:
        username = query.strip().lstrip('@')
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(
                    f"https://vk.com/{username}",
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                )
                if resp.status_code == 200 and 'page_not_found' not in str(resp.url):
                    text = resp.text
                    # Ищем имя
                    name_match = re.search(r'<title>(.*?)\s*[|—].*?VK</title>', text)
                    name = name_match.group(1).strip() if name_match else "Неизвестно"

                    # Ищем ID
                    id_match = re.search(r'"id":(\d+)', text)
                    uid = id_match.group(1) if id_match else None

                    # Проверяем закрытость
                    is_closed = 'profile_closed' in text or 'ProfileBlocked' in text

                    result += f"✅ Профиль найден!\n"
                    result += f"👤 Имя: {name}\n"
                    if uid:
                        result += f"🆔 ID: {uid}\n"
                    result += f"🔗 Ссылка: vk.com/{username}\n"
                    result += f"🔒 Закрытый: {'Да' if is_closed else 'Нет'}\n\n"
                else:
                    result += f"❌ Профиль @{username} не найден\n\n"
        except Exception as e:
            result += f"⚠️ Не удалось проверить профиль\n\n"

    # Даём ссылки для ручного поиска
    result += f"🔗 Поиск вручную:\n"
    result += f"• [Поиск людей ВКонтакте](https://vk.com/search?c[section]=people&c[q]={query_enc})\n"
    if is_username:
        username = query.strip().lstrip('@')
        result += f"• [Прямая ссылка](https://vk.com/{username}) — профиль @{username}\n"
    result += f"• [VK People Search](https://vk.com/search?c[section]=people&c[q]={query_enc}&c[country]=1) — только Россия"

    return result

async def search_image_info(url: str) -> str:
    encoded = url.replace(':', '%3A').replace('/', '%2F')
    return (
        f"🖼 Обратный поиск изображения\n\n"
        f"🔗 Открой для поиска:\n\n"
        f"• [Яндекс Картинки](https://yandex.ru/images/search?url={url}&rpt=imageview) — лучший для RU\n"
        f"• [Google Lens](https://lens.google.com/uploadbyurl?url={url}) — Google\n"
        f"• [TinEye](https://tineye.com/search?url={url}) — точный поиск копий\n"
        f"• [Bing Visual](https://www.bing.com/images/search?q=imgurl:{url}&view=detailv2) — Microsoft\n\n"
        f"💡 Для лучшего результата загрузи фото напрямую на Яндекс Картинки!"
    )

# ─── Хендлеры ────────────────────────────────────────────────────────────────
dp = Dispatcher(storage=MemoryStorage())

def get_requests_left(user_id: int) -> int:
    if is_admin(user_id):
        return 999
    user = get_user(user_id)
    return user["requests_left"] if user else 0

@dp.message(CommandStart())
async def cmd_start(msg: Message):
    upsert_user(msg.from_user.id, msg.from_user.username or "")
    rl = get_requests_left(msg.from_user.id)
    bal = "∞" if is_admin(msg.from_user.id) else str(rl)
    await msg.answer(
        f"🕵️ Привет, <b>{msg.from_user.first_name}</b>!\n\n"
        f"Я OSINT-бот — помогаю искать открытую информацию.\n\n"
        f"🔍 Поиск по нику — 20+ сайтов\n"
        f"📱 Номер телефона — оператор и страна\n"
        f"📧 Email — проверка утечек\n"
        f"🖼 Reverse Image — поиск по фото\n\n"
        f"💡 Баланс: <b>{bal} запросов</b>",
        parse_mode="HTML",
        reply_markup=kb_main(msg.from_user.id, rl)
    )

@dp.callback_query(F.data == "back_main")
async def cb_back(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    rl = get_requests_left(cb.from_user.id)
    await cb.message.edit_text(
        "🕵️ Главное меню:",
        reply_markup=kb_main(cb.from_user.id, rl)
    )
    await cb.answer()

@dp.callback_query(F.data == "my_balance")
async def cb_balance(cb: CallbackQuery):
    user_id = cb.from_user.id
    if is_admin(user_id):
        await cb.answer("👑 Администратор — безлимитный доступ!", show_alert=True)
        return
    user = get_user(user_id)
    await cb.message.edit_text(
        f"📊 <b>Твой баланс</b>\n\n"
        f"💰 Осталось запросов: <b>{user['requests_left']}</b>\n"
        f"📦 Куплено всего: <b>{user['total_purchased']}</b>\n\n"
        f"Запросы не сгорают — используй когда удобно! 👇",
        parse_mode="HTML",
        reply_markup=kb_buy()
    )
    await cb.answer()

@dp.callback_query(F.data == "buy_requests")
async def cb_buy_requests(cb: CallbackQuery):
    await cb.message.edit_text(
        "💎 <b>Купить запросы</b>\n\n"
        "Запросы не сгорают!\n\n"
        "20 запросов — 50 ⭐ (~60₽)\n"
        "50 запросов — 100 ⭐ (~120₽)\n"
        "100 запросов — 180 ⭐ (~220₽)\n"
        "250 запросов — 350 ⭐ (~430₽)",
        parse_mode="HTML",
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
        description=f"{pack['requests']} запросов к OSINT инструментам. Не сгорают!",
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
    payload = msg.successful_payment.invoice_payload
    pack_key = payload.replace("osint_", "")
    pack = PACKAGES.get(pack_key)
    if pack:
        add_requests(msg.from_user.id, pack["requests"])
        rl = get_requests_left(msg.from_user.id)
        await msg.answer(
            f"✅ <b>Оплата прошла!</b>\n\n"
            f"Добавлено: <b>+{pack['requests']} запросов</b>\n"
            f"Баланс: <b>{rl} запросов</b>\n\n"
            f"Удачи в поиске! 🕵️",
            parse_mode="HTML",
            reply_markup=kb_main(msg.from_user.id, rl)
        )

# ─── Поиск по нику ───────────────────────────────────────────────────────────
@dp.callback_query(F.data == "search_username")
async def cb_search_username(cb: CallbackQuery, state: FSMContext):
    if not can_search(cb.from_user.id):
        await cb.message.edit_text("⛔ Запросы закончились!\n\nКупи ещё 👇", reply_markup=kb_buy())
        await cb.answer()
        return
    await state.set_state(SearchStates.waiting_username)
    await cb.message.edit_text(
        "🔍 <b>Поиск по нику</b>\n\n"
        "Введи username:\n"
        "<i>Пример: durov, elonmusk</i>",
        parse_mode="HTML", reply_markup=kb_back()
    )
    await cb.answer()

@dp.message(SearchStates.waiting_username)
async def handle_username(msg: Message, state: FSMContext):
    await state.clear()
    username = msg.text.strip().lstrip('@')
    if len(username) < 2:
        await msg.answer("❌ Слишком короткий ник", reply_markup=kb_back())
        return
    use_request(msg.from_user.id)
    log_search(msg.from_user.id, "username", username)
    wait = await msg.answer(f"🔍 Ищу <b>{username}</b> на 20 сайтах... ⏳", parse_mode="HTML")
    result = await search_username(username)
    rl = get_requests_left(msg.from_user.id)
    await wait.delete()
    await msg.answer(result, parse_mode="Markdown", disable_web_page_preview=True)
    await msg.answer(f"💰 Осталось: {rl} запр.", reply_markup=kb_main(msg.from_user.id, rl))

# ─── Проверка телефона ───────────────────────────────────────────────────────
@dp.callback_query(F.data == "search_phone")
async def cb_search_phone(cb: CallbackQuery, state: FSMContext):
    if not can_search(cb.from_user.id):
        await cb.message.edit_text("⛔ Запросы закончились!\n\nКупи ещё 👇", reply_markup=kb_buy())
        await cb.answer()
        return
    await state.set_state(SearchStates.waiting_phone)
    await cb.message.edit_text(
        "📱 <b>Анализ номера телефона</b>\n\n"
        "Введи номер:\n"
        "<i>Примеры: +79161234567, 89161234567</i>",
        parse_mode="HTML", reply_markup=kb_back()
    )
    await cb.answer()

@dp.message(SearchStates.waiting_phone)
async def handle_phone(msg: Message, state: FSMContext):
    await state.clear()
    use_request(msg.from_user.id)
    log_search(msg.from_user.id, "phone", msg.text.strip())
    result = await search_phone(msg.text.strip())
    rl = get_requests_left(msg.from_user.id)
    await msg.answer(result, parse_mode="Markdown", disable_web_page_preview=True)
    await msg.answer(f"💰 Осталось: {rl} запр.", reply_markup=kb_main(msg.from_user.id, rl))

# ─── Проверка email ──────────────────────────────────────────────────────────
@dp.callback_query(F.data == "search_email")
async def cb_search_email(cb: CallbackQuery, state: FSMContext):
    if not can_search(cb.from_user.id):
        await cb.message.edit_text("⛔ Запросы закончились!\n\nКупи ещё 👇", reply_markup=kb_buy())
        await cb.answer()
        return
    await state.set_state(SearchStates.waiting_email)
    await cb.message.edit_text(
        "📧 <b>Проверка email</b>\n\n"
        "Введи email:\n"
        "<i>Пример: example@gmail.com</i>",
        parse_mode="HTML", reply_markup=kb_back()
    )
    await cb.answer()

@dp.message(SearchStates.waiting_email)
async def handle_email(msg: Message, state: FSMContext):
    await state.clear()
    email = msg.text.strip().lower()
    use_request(msg.from_user.id)
    log_search(msg.from_user.id, "email", email)
    result = await search_email(email)
    rl = get_requests_left(msg.from_user.id)
    await msg.answer(result, parse_mode="Markdown", disable_web_page_preview=True)
    await msg.answer(f"💰 Осталось: {rl} запр.", reply_markup=kb_main(msg.from_user.id, rl))

# ─── Поиск ВКонтакте ────────────────────────────────────────────────────────
@dp.callback_query(F.data == "search_vk")
async def cb_search_vk(cb: CallbackQuery, state: FSMContext):
    if not can_search(cb.from_user.id):
        await cb.message.edit_text("⛔ Запросы закончились!\n\nКупи ещё 👇", reply_markup=kb_buy())
        await cb.answer()
        return
    await state.set_state(SearchStates.waiting_vk)
    await cb.message.edit_text(
        "👤 <b>Поиск ВКонтакте</b>\n\n"
        "Введи имя, фамилию или username:\n"
        "<i>Примеры: durov, Иван Петров, ivan_petrov</i>",
        parse_mode="HTML", reply_markup=kb_back()
    )
    await cb.answer()

@dp.message(SearchStates.waiting_vk)
async def handle_vk(msg: Message, state: FSMContext):
    await state.clear()
    query = msg.text.strip()
    use_request(msg.from_user.id)
    log_search(msg.from_user.id, "vk", query)
    wait = await msg.answer("👤 Ищу ВКонтакте... ⏳")
    result = await search_vk(query)
    rl = get_requests_left(msg.from_user.id)
    await wait.delete()
    await msg.answer(result, parse_mode="Markdown", disable_web_page_preview=True)
    await msg.answer(f"💰 Осталось: {rl} запр.", reply_markup=kb_main(msg.from_user.id, rl))

# ─── Reverse Image Search ────────────────────────────────────────────────────
@dp.callback_query(F.data == "search_image")
async def cb_search_image(cb: CallbackQuery, state: FSMContext):
    if not can_search(cb.from_user.id):
        await cb.message.edit_text("⛔ Запросы закончились!\n\nКупи ещё 👇", reply_markup=kb_buy())
        await cb.answer()
        return
    await state.set_state(SearchStates.waiting_image)
    await cb.message.edit_text(
        "🖼 <b>Reverse Image Search</b>\n\n"
        "Отправь фото или ссылку на изображение:\n"
        "<i>Пример: https://example.com/photo.jpg</i>",
        parse_mode="HTML", reply_markup=kb_back()
    )
    await cb.answer()

@dp.message(SearchStates.waiting_image)
async def handle_image(msg: Message, state: FSMContext, bot: Bot):
    await state.clear()
    use_request(msg.from_user.id)

    if msg.photo:
        file = await bot.get_file(msg.photo[-1].file_id)
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        log_search(msg.from_user.id, "image", "photo_upload")
    elif msg.text and msg.text.startswith("http"):
        url = msg.text.strip()
        log_search(msg.from_user.id, "image", url)
    else:
        await msg.answer("❌ Отправь ссылку на фото или само фото", reply_markup=kb_back())
        return

    result = await search_image_info(url)
    rl = get_requests_left(msg.from_user.id)
    await msg.answer(result, parse_mode="Markdown", disable_web_page_preview=True)
    await msg.answer(f"💰 Осталось: {rl} запр.", reply_markup=kb_main(msg.from_user.id, rl))

# ─── Админ-панель ────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Нет доступа", show_alert=True)
        return
    stats = get_stats()
    top = "\n".join([f"  • {t[0]}: {t[1]} раз" for t in stats["top_types"]]) or "  Нет данных"
    await cb.message.edit_text(
        f"👑 <b>Админ-панель</b>\n\n"
        f"👥 Пользователей: <b>{stats['total']}</b>\n"
        f"💰 Платящих: <b>{stats['paid']}</b>\n"
        f"🔍 Поисков всего: <b>{stats['searches']}</b>\n\n"
        f"📊 Популярные запросы:\n{top}\n\n"
        f"Выдать запросы:\n"
        f"<code>/give USER_ID количество</code>",
        parse_mode="HTML",
        reply_markup=kb_admin()
    )
    await cb.answer()

@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Нет доступа", show_alert=True)
        return
    stats = get_stats()
    await cb.answer(
        f"👥 {stats['total']} польз. | 💰 {stats['paid']} платящих | 🔍 {stats['searches']} поисков",
        show_alert=True
    )

@dp.callback_query(F.data == "admin_give")
async def cb_admin_give(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("⛔ Нет доступа", show_alert=True)
        return
    await cb.message.edit_text(
        "🎁 <b>Выдать запросы</b>\n\n"
        "<code>/give USER_ID количество</code>\n"
        "Пример: <code>/give 123456789 50</code>",
        parse_mode="HTML",
        reply_markup=kb_admin()
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
        target_id = int(args[1])
        count = int(args[2])
    except ValueError:
        await msg.answer("❌ Неверный формат")
        return
    user = get_user(target_id)
    if not user:
        await msg.answer(f"❌ Пользователь {target_id} не найден")
        return
    add_requests(target_id, count)
    user = get_user(target_id)
    await msg.answer(f"✅ Пользователю {target_id} добавлено {count} запросов. Баланс: {user['requests_left']}")
    try:
        await msg.bot.send_message(target_id, f"🎁 Тебе добавлено {count} запросов! Баланс: {user['requests_left']}")
    except:
        pass

# ─── Запуск ──────────────────────────────────────────────────────────────────
async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN)
    log.info("OSINT бот запущен ✅")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
