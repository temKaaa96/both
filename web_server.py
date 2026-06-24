"""
web_server.py — отдаёт сгенерированные отчёты по ссылке /r/<token>.

Хранит HTML и срок жизни в том же SQLite (users.db, Railway-том), срок 24 ч
проверяется на сервере. По истёкшей ссылке — страница «Срок действия истёк».

Запускается в одном процессе с ботом (см. main() в bot.py).
Требует web-процесс на Railway (Procfile: web:) — тогда есть публичный домен.
"""

import os
import sqlite3
import secrets
import time

from aiohttp import web

from report_html import expired_page

DB_PATH  = "users.db"
TTL_SECS = 24 * 3600


def init_reports_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            token   TEXT PRIMARY KEY,
            html    TEXT,
            created REAL,
            expires REAL
        )
    """)
    con.commit()
    con.close()


def save_report(html: str, ttl: int = TTL_SECS) -> tuple[str, float]:
    """Сохраняет отчёт, возвращает (token, expires_epoch)."""
    token = secrets.token_urlsafe(9)
    now = time.time()
    exp = now + ttl
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT INTO reports (token, html, created, expires) VALUES (?,?,?,?)",
                (token, html, now, exp))
    con.commit()
    con.close()
    return token, exp


def _purge_expired():
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM reports WHERE expires < ?", (time.time(),))
        con.commit()
        con.close()
    except Exception:
        pass


def report_url(token: str) -> str:
    base = os.environ.get("BASE_URL", "").strip()
    if not base:
        dom = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        base = f"https://{dom}" if dom else ""
    base = base.rstrip("/")
    return f"{base}/r/{token}" if base else f"/r/{token}"


# ─── HTTP-роуты ─────────────────────────────────────────────────────────
async def handle_report(request: web.Request) -> web.Response:
    token = request.match_info.get("token", "")
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT html, expires FROM reports WHERE token=?", (token,)).fetchone()
    con.close()
    if not row:
        return web.Response(text=expired_page(), content_type="text/html",
                            charset="utf-8", status=404)
    html_text, expires = row
    if time.time() > expires:
        return web.Response(text=expired_page(), content_type="text/html",
                            charset="utf-8", status=410)
    return web.Response(text=html_text, content_type="text/html", charset="utf-8")


async def handle_root(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def start_web():
    """Запускает aiohttp-сервер на 0.0.0.0:$PORT (Railway задаёт PORT)."""
    init_reports_db()
    _purge_expired()
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/health", handle_root)
    app.router.add_get("/r/{token}", handle_report)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    return runner
