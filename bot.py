"""
Telegram-бот: проверка контрагента (ЕГРЮЛ + ГИР БО) и отчёты по домену/IP.
Стек: Python 3.10+, aiogram 3, SQLite. Только открытые официальные источники.
"""

import asyncio
import logging
import sqlite3
import re
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery,
    BufferedInputFile,
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import httpx

from config import BOT_TOKEN, ADMIN_ID
from company_lookup import (fetch_company, fetch_financials, fetch_location_map,
                            build_company_spec, build_company_report)
from report_html import generate_report_html
from web_server import init_reports_db, save_report, report_url, start_web
from web_lookup import fetch_ip, build_ip_report, fetch_domain, build_domain_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ─── Пакеты запросов ─────────────────────────────────────────────────────────
PACKAGES = {
    "pack_20":  {"requests": 20,  "stars": 50,  "label": "20 запросов"},
    "pack_50":  {"requests": 50,  "stars": 100, "label": "50 запросов"},
    "pack_100": {"requests": 100, "stars": 180, "label": "100 запросов"},
    "pack_250": {"requests": 250, "stars": 350, "label": "250 запросов"},
}

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
    inn = State()
    ip  = State()

# ─── Клавиатуры ──────────────────────────────────────────────────────────────
def kb_main(user_id: int, rl: int) -> InlineKeyboardMarkup:
    bal = "∞" if is_admin(user_id) else str(rl)
    rows = []
    if is_admin(user_id):
        rows.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    rows += [
        [InlineKeyboardButton(text="🏢 Проверка контрагента (ИНН/ОГРН)", callback_data="s_inn")],
        [InlineKeyboardButton(text="🌐 Отчёт по домену / IP",            callback_data="s_ip")],
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

# ─── Шрифты для PDF (кириллица) ──────────────────────────────────────────────
FONT_PATH      = "/tmp/DejaVuSans.ttf"
FONT_BOLD_PATH = "/tmp/DejaVuSans-Bold.ttf"
FONT_URL       = "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans.ttf"
FONT_BOLD_URL  = "https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans-Bold.ttf"

async def ensure_fonts():
    """Скачивает шрифт DejaVu в /tmp, если его нет или он повреждён."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for path, url in [(FONT_PATH, FONT_URL), (FONT_BOLD_PATH, FONT_BOLD_URL)]:
            if not os.path.exists(path) or os.path.getsize(path) < 50_000:
                log.info(f"Скачиваю шрифт: {url}")
                r = await client.get(url)
                r.raise_for_status()
                with open(path, "wb") as f:
                    f.write(r.content)

# ─── Диспетчер и приветствие ─────────────────────────────────────────────────
dp = Dispatcher(storage=MemoryStorage())

WELCOME = (
    "🏢 *Проверка контрагента и инфраструктуры*\n\n"
    "Отчёты по открытым официальным данным — в виде PDF.\n\n"
    "📌 *Что доступно:*\n\n"
    "🏢 *Контрагент* — по ИНН/ОГРН: реквизиты ЕГРЮЛ, руководитель, "
    "учредители, ОКВЭД, финансы (ГИР БО), признаки для проверки\n"
    "🌐 *Домен / IP* — регистрация (RDAP), DNS, хостинг, гео\n\n"
    "Примеры: `7707083893`, `example.com`, `8.8.8.8`\n\n"
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

# ─── ИНН / ОГРН — проверка контрагента ───────────────────────────────────────
@dp.callback_query(F.data == "s_inn")
async def cb_inn(cb: CallbackQuery, state: FSMContext):
    await check_access(cb, state, S.inn,
        "🏢 *Проверка контрагента*\n\nВведи ИНН или ОГРН компании / ИП:\n_Пример: 7707083893_")

@dp.message(S.inn)
async def h_inn(msg: Message, state: FSMContext):
    await state.clear()
    q = msg.text.strip()
    log_search(msg.from_user.id, "company", q)
    w = await msg.answer("🏢 Запрашиваю ЕГРЮЛ и ГИР БО... ⏳")
    data = await fetch_company(q)
    if not data:
        await w.delete()
        await msg.answer("❌ Компания не найдена в реестре ЕГРЮЛ", reply_markup=kb_back())
    else:
        finance = await fetch_financials(data.get("inn") or q)
        map_img = await fetch_location_map(data)
        spec = build_company_spec(data, finance=finance, map_img=map_img)
        now = datetime.now()
        exp = now + timedelta(hours=24)
        html = generate_report_html(
            **spec,
            created_str=now.strftime("%d.%m.%Y %H:%M"),
            expires_str=exp.strftime("%d.%m.%Y %H:%M"),
        )
        token, _ = save_report(html)
        url = report_url(token)
        await w.delete()
        use_request(msg.from_user.id)
        if url.startswith("http"):
            await msg.answer(
                f"✅ *Отчёт готов*\n\n🔗 {url}\n\n"
                f"⏳ Ссылка действует *24 часа* — до {exp.strftime('%d.%m.%Y %H:%M')}.",
                parse_mode="Markdown", disable_web_page_preview=True)
        else:
            await msg.answer(
                "⚠️ Публичная ссылка не настроена. Задай переменную BASE_URL "
                "или запусти как web-процесс (RAILWAY_PUBLIC_DOMAIN).",
                reply_markup=kb_back())
    rl  = get_requests_left(msg.from_user.id)
    bal = "∞" if is_admin(msg.from_user.id) else str(rl)
    await msg.answer(f"💰 Баланс: *{bal} запросов*", parse_mode="Markdown",
                     reply_markup=kb_main(msg.from_user.id, rl))


# ─── IP / Домен ──────────────────────────────────────────────────────────────
@dp.callback_query(F.data == "s_ip")
async def cb_ip(cb: CallbackQuery, state: FSMContext):
    await check_access(cb, state, S.ip,
        "🌐 *Отчёт по домену / IP*\n\nВведи IP-адрес или домен:\n_Примеры: 8.8.8.8 или example.com_")

@dp.message(S.ip)
async def h_ip(msg: Message, state: FSMContext):
    await state.clear()
    q = msg.text.strip()
    log_search(msg.from_user.id, "ip_domain", q)
    w = await msg.answer("🌐 Анализирую... ⏳")
    await ensure_fonts()
    if re.match(r'^\d{1,3}(\.\d{1,3}){3}$', q):
        data = await fetch_ip(q)
        pdf = build_ip_report(q, data) if data else None
    else:
        info = await fetch_domain(q)
        pdf = build_domain_report(info) if (info.get("rdap") or info.get("ip")) else None
    await w.delete()
    if not pdf:
        await msg.answer("❌ Не удалось получить данные", reply_markup=kb_back())
    else:
        use_request(msg.from_user.id)
        await msg.answer_document(
            BufferedInputFile(pdf, filename=f"{re.sub(r'[^a-zA-Z0-9.]', '_', q)[:20]}.pdf"),
            caption="📄 *Отчёт по инфраструктуре*", parse_mode="Markdown")
    rl  = get_requests_left(msg.from_user.id)
    bal = "∞" if is_admin(msg.from_user.id) else str(rl)
    await msg.answer(f"💰 Баланс: *{bal} запросов*", parse_mode="Markdown",
                     reply_markup=kb_main(msg.from_user.id, rl))

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
    init_reports_db()
    bot = Bot(token=BOT_TOKEN)
    runner = await start_web()          # aiohttp на 0.0.0.0:$PORT
    log.info("Бот + веб-сервер отчётов запущены ✅")
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
