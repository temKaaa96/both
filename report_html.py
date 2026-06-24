"""
report_html.py — рендер отчёта в HTML-страницу в том же светлом стиле,
что и PDF (боковая навигация, карточки, таблицы, теги, картинки).

Принимает тот же «spec», что и generate_report_pdf:
  title, subtitle, summary_pairs, sections, brand, hero_image, nav, disclaimer.
Картинки (bytes/путь) встраиваются как data-URI — страница самодостаточна.
"""

import base64
import html
import io
import os
from typing import Optional, List, Tuple, Dict


def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


def _data_uri(data) -> Optional[str]:
    try:
        if isinstance(data, (bytes, bytearray)):
            raw = bytes(data)
        elif isinstance(data, str) and os.path.exists(data):
            with open(data, "rb") as f:
                raw = f.read()
        else:
            return None
        fmt = "png"
        if raw[:3] == b"\xff\xd8\xff":
            fmt = "jpeg"
        elif raw[:4] == b"GIF8":
            fmt = "gif"
        return f"data:image/{fmt};base64," + base64.b64encode(raw).decode()
    except Exception:
        return None


_CSS = """
:root{
  --page-bg:#f5f6f8; --card:#fff; --border:#e4e6ea; --dark:#181c23;
  --mid:#787e86; --label:#8c929a; --shade:#f8f9fa; --divider:#ebedf0;
  --tag-bg:#f0f2f5; --tag-bd:#e0e3e7; --tag-fg:#373e47; --accent:#1e293b;
}
*{box-sizing:border-box} html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--page-bg);color:var(--dark);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-size:14px;line-height:1.45}
a{color:inherit;text-decoration:none}
.page{max-width:1040px;margin:0 auto;padding:18px 20px 40px}
.topbar{display:flex;align-items:center;gap:8px;padding:6px 2px 14px;
  border-bottom:1px solid var(--divider);margin-bottom:18px}
.logo-dot{width:14px;height:14px;border-radius:50%;background:var(--accent);display:inline-block}
.topbar b{font-size:17px} .muted{color:var(--mid)}
.layout{display:flex;gap:24px;align-items:flex-start}
.sidebar{flex:0 0 200px;position:sticky;top:18px}
.nav-title{font-weight:700;font-size:15px;margin:6px 4px 10px}
.sidebar a{display:block;padding:6px 8px;border-radius:8px;color:var(--mid);font-size:13.5px}
.sidebar a:hover{background:#eceef1;color:var(--dark)}
.content{flex:1;min-width:0}
.card{background:var(--card);border:1px solid var(--border);border-radius:14px;
  padding:18px 20px;margin-bottom:16px}
.hero h1{font-size:30px;margin:0 0 4px;letter-spacing:-.5px;word-break:break-word}
.hero .sub{color:var(--mid);font-size:15px;margin-bottom:14px}
.hero-head{display:flex;gap:16px;align-items:center;margin-bottom:6px}
.avatar{flex:0 0 64px;width:64px;height:64px;border-radius:14px;background:var(--shade);
  object-fit:contain;border:1px solid var(--border)}
h2{font-size:18px;margin:0 0 12px;display:flex;align-items:baseline;gap:8px}
h2 .sub{font-size:12.5px;color:var(--label);font-weight:400}
.sec-divider{border:0;border-top:1px solid var(--divider);margin:0 0 12px}
table{width:100%;border-collapse:collapse}
td{padding:8px 10px;vertical-align:top;font-size:13.5px}
tr:nth-child(even){background:var(--shade)}
td.k{color:var(--label);width:230px;white-space:nowrap}
td.v{color:var(--dark);word-break:break-word}
.tags{display:flex;flex-wrap:wrap;gap:8px}
.tag{background:var(--tag-bg);border:1px solid var(--tag-bd);color:var(--tag-fg);
  border-radius:8px;padding:6px 10px;font-size:12.5px}
figure{margin:0;text-align:center}
figure img{max-width:100%;border-radius:10px;border:1px solid var(--border)}
figcaption{color:var(--mid);font-size:12px;margin-top:6px}
.note{color:var(--mid);font-size:13px}
.disclaimer{color:var(--mid);font-size:11px;text-align:center;margin:8px 4px 0}
.foot{border-top:1px solid var(--divider);margin-top:20px;padding-top:10px;
  display:flex;justify-content:space-between;color:var(--mid);font-size:11.5px}
@media(max-width:760px){.layout{flex-direction:column}.sidebar{position:static;flex:none;width:100%}
  .sidebar a{display:inline-block}}
"""

_EXPIRED_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Срок действия истёк</title><style>{css}
.box{{max-width:440px;margin:18vh auto;text-align:center;background:var(--card);
border:1px solid var(--border);border-radius:14px;padding:40px 28px}}
.box h1{{font-size:22px;margin:14px 0 6px}}.box p{{color:var(--mid);margin:0}}
</style></head><body><div class="box"><div class="logo-dot" style="width:18px;height:18px;margin:auto"></div>
<h1>Срок действия истёк</h1><p>Ссылка на отчёт действовала 24 часа и больше недоступна.</p></div></body></html>
""".format(css=_CSS)


def expired_page() -> str:
    return _EXPIRED_HTML


def generate_report_html(
    title: str,
    subtitle: str = "",
    summary_pairs: Optional[List[Tuple[str, str]]] = None,
    sections: Optional[List[Dict]] = None,
    brand: Tuple[str, str] = ("Бизнес", "отчёт"),
    hero_image=None,
    nav: bool = True,
    disclaimer: Optional[str] = None,
    created_str: str = "",
    expires_str: str = "",
    logo_path=None,        # принимается для совместимости со spec, в HTML не нужен
) -> str:
    sections = sections or []
    summary_pairs = summary_pairs or []

    # навигация
    nav_links = []
    if summary_pairs or True:
        nav_links.append(("Сводка", "summary"))
    for i, sec in enumerate(sections):
        if sec.get("title"):
            nav_links.append((sec["title"], f"sec-{i}"))

    def kv_table(pairs):
        rows = "".join(
            f'<tr><td class="k">{_esc(k)}</td><td class="v">{_esc(v)}</td></tr>'
            for k, v in pairs)
        return f"<table>{rows}</table>"

    # hero
    avatar = ""
    uri = _data_uri(hero_image) if hero_image else None
    if uri:
        avatar = f'<img class="avatar" src="{uri}" alt="logo">'
    hero = (
        f'<section class="card hero" id="summary">'
        f'<div class="hero-head">{avatar}<div>'
        f'<h1>{_esc(title)}</h1>'
        + (f'<div class="sub">{_esc(subtitle)}</div>' if subtitle else "")
        + '</div></div>'
    )
    if summary_pairs:
        hero += '<h2>Краткая сводка</h2><hr class="sec-divider">' + kv_table(summary_pairs)
    hero += '</section>'

    # секции
    body_secs = []
    for i, sec in enumerate(sections):
        parts = [f'<section class="card" id="sec-{i}">']
        if sec.get("title"):
            sub = f' <span class="sub">{_esc(sec["subtitle"])}</span>' if sec.get("subtitle") else ""
            parts.append(f'<h2>{_esc(sec["title"])}{sub}</h2><hr class="sec-divider">')
        if sec.get("pairs"):
            parts.append(kv_table(sec["pairs"]))
        if sec.get("tags"):
            chips = "".join(f'<span class="tag">{_esc(t)}</span>' for t in sec["tags"])
            parts.append(f'<div class="tags">{chips}</div>')
        if sec.get("image"):
            iuri = _data_uri(sec["image"])
            if iuri:
                cap = f'<figcaption>{_esc(sec.get("caption",""))}</figcaption>' if sec.get("caption") else ""
                parts.append(f'<figure><img src="{iuri}" alt="">{cap}</figure>')
        if sec.get("note"):
            parts.append(f'<p class="note">{_esc(sec["note"])}</p>')
        parts.append('</section>')
        body_secs.append("".join(parts))

    sidebar = ""
    if nav:
        links = "".join(f'<a href="#{anchor}">{_esc(label)}</a>' for label, anchor in nav_links)
        sidebar = f'<nav class="sidebar"><div class="nav-title">Навигация</div>{links}</nav>'

    disc = f'<p class="disclaimer">{_esc(disclaimer)}</p>' if disclaimer else ""
    foot = (f'<footer class="foot"><span>Сформировано: {_esc(created_str)}</span>'
            f'<span>Действителен до: {_esc(expires_str)}</span></footer>')

    bd, bl = brand
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{_esc(title)}</title><style>{_CSS}</style></head><body>'
        '<div class="page">'
        f'<header class="topbar"><span class="logo-dot"></span>'
        f'<b>{_esc(bd)}</b> <span class="muted">{_esc(bl)}</span></header>'
        f'<div class="layout">{sidebar}'
        f'<main class="content">{hero}{"".join(body_secs)}{disc}</main></div>'
        f'{foot}</div></body></html>'
    )
