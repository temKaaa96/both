"""
report_pdf.py — чистый PDF-отчёт в светлом «report»-стиле.

Возможности:
  • светлые карточки, краткая сводка, таблицы ключ-значение, чипы-теги;
  • боковая НАВИГАЦИЯ с кликабельными ссылками по разделам (nav=True);
  • логотип в шапке (logo_path) и аватар/логотип в hero (hero_image);
  • секции-картинки (логотип компании, карта местоположения и т.п.).

Назначение: ЛЕГАЛЬНЫЕ данные о юрлицах и инфраструктуре. Не для досье на людей.
Зависимости: fpdf2, Pillow; шрифты DejaVu в /tmp (ensure_fonts()).
"""

import io
import os
import re
from datetime import datetime
from typing import Optional, Sequence, Tuple, List, Dict, Union

from fpdf import FPDF, XPos, YPos

# ─── Шрифты ──────────────────────────────────────────────────────────────
FONT_PATH      = "/tmp/DejaVuSans.ttf"
FONT_BOLD_PATH = "/tmp/DejaVuSans-Bold.ttf"

# ─── Палитра ─────────────────────────────────────────────────────────────
PAGE_BG     = (245, 246, 248)
CARD_BG     = (255, 255, 255)
CARD_BORDER = (228, 230, 234)
TEXT_DARK   = (24,  28,  35)
TEXT_MID    = (120, 126, 134)
TEXT_LABEL  = (140, 146, 154)
ROW_SHADE   = (248, 249, 250)
DIVIDER     = (235, 237, 240)
TAG_BG      = (240, 242, 245)
TAG_BORDER  = (224, 227, 231)
TAG_FG      = (55,  62,  71)
ACCENT_DEF  = (30,  41,  59)

# ─── Геометрия (A4) ──────────────────────────────────────────────────────
PAGE_W, PAGE_H = 210, 297
MARGIN         = 14
CARD_PAD       = 6
KEY_W          = 50
FOOTER_SPACE   = 16
SIDEBAR_W      = 46


def _clean(text) -> str:
    if text is None:
        return ""
    text = str(text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    text = re.sub(r'[*_`#]', '', text)
    text = re.sub(
        r'[\U0001F000-\U0001FAFF\U00002600-\U000027BF'
        r'\U0001F900-\U0001F9FF\u200d\ufe0f\u20e3]+',
        '', text, flags=re.UNICODE)
    return re.sub(r'  +', ' ', text).strip()


def _img_meta(data) -> Optional[tuple]:
    """Готовит изображение для fpdf. Возвращает (src, w_px, h_px) или None."""
    try:
        from PIL import Image
        if isinstance(data, (bytes, bytearray)):
            raw = bytes(data)
            with Image.open(io.BytesIO(raw)) as im:
                w, h = im.size
            return (io.BytesIO(raw), w, h)
        if isinstance(data, str) and os.path.exists(data):
            with Image.open(data) as im:
                w, h = im.size
            return (data, w, h)
    except Exception:
        pass
    return None


def _wrap_lines(pdf, text: str, width: float) -> List[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if pdf.get_string_width(trial) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


_TAG_H, _TAG_GAP_X, _TAG_GAP_Y = 8.0, 3.0, 2.0


def _tags_height(pdf, items, max_w) -> float:
    pdf.set_font("Reg", size=9)
    x, lines = 0.0, 1
    for it in items:
        tw = pdf.get_string_width(it) + 6
        if x + tw > max_w and x > 0:
            x = 0
            lines += 1
        x += tw + _TAG_GAP_X
    return lines * _TAG_H + (lines - 1) * _TAG_GAP_Y


def _draw_tags(pdf, x0, y0, items, max_w) -> float:
    pdf.set_font("Reg", size=9)
    x, y = x0, y0
    for it in items:
        tw = pdf.get_string_width(it) + 6
        if x + tw > x0 + max_w and x > x0:
            x = x0
            y += _TAG_H + _TAG_GAP_Y
        pdf.set_draw_color(*TAG_BORDER)
        pdf.set_fill_color(*TAG_BG)
        pdf.rect(x, y, tw, _TAG_H - 0.5, style="FD")
        pdf.set_xy(x + 3, y + 1)
        pdf.set_text_color(*TAG_FG)
        pdf.cell(tw - 6, _TAG_H - 2.5, it)
        x += tw + _TAG_GAP_X
    return y + _TAG_H


class ReportPDF(FPDF):
    def __init__(self, brand_dark="Бизнес", brand_light="отчёт", accent=ACCENT_DEF,
                 logo_path=None, nav=False, **kw):
        super().__init__(**kw)
        self.brand_dark  = brand_dark
        self.brand_light = brand_light
        self.accent      = accent
        self.logo_path   = logo_path if (logo_path and os.path.exists(logo_path)) else None
        self.nav         = nav
        self.nav_items   = []          # [(label, link_id)]
        self.gen_str     = datetime.now().strftime("%d.%m.%Y %H:%M")
        # ширина контентной зоны (сдвигается, если есть навигация)
        self.cx       = MARGIN + (SIDEBAR_W + 4 if nav else 0)
        self.cw       = PAGE_W - MARGIN - self.cx
        self.inner_w  = self.cw - 2 * CARD_PAD

    def header(self):
        self.set_fill_color(*PAGE_BG)
        self.rect(0, 0, PAGE_W, PAGE_H, style="F")

        # логотип в шапке
        if self.logo_path:
            lh = 8.0
            meta = _img_meta(self.logo_path)
            lw = min(lh * meta[1] / meta[2], 60) if meta else lh
            self.image(self.logo_path, x=MARGIN, y=8.5, h=lh)
            tx = MARGIN + lw + 3
        else:
            self.set_fill_color(*self.accent)
            self.ellipse(MARGIN, 10.3, 4.2, 4.2, style="F")
            tx = MARGIN + 6.5
        if self.brand_dark or self.brand_light:
            self.set_xy(tx, 9)
            self.set_font("Bold", size=12); self.set_text_color(*TEXT_DARK)
            if self.brand_dark:
                self.cell(self.get_string_width(self.brand_dark) + 1, 7, self.brand_dark)
            self.set_font("Reg", size=12); self.set_text_color(*TEXT_MID)
            if self.brand_light:
                self.cell(40, 7, (" " if self.brand_dark else "") + self.brand_light)
        self.set_draw_color(*DIVIDER)
        self.line(MARGIN, 18, PAGE_W - MARGIN, 18)

        self._draw_sidebar()
        self.set_y(22)

    def _draw_sidebar(self):
        if not (self.nav and self.nav_items):
            return
        nx, ny = MARGIN, 26
        self.set_xy(nx, ny)
        self.set_font("Bold", size=9.5); self.set_text_color(*TEXT_DARK)
        self.cell(SIDEBAR_W, 6, "Навигация")
        ny += 9
        self.set_font("Reg", size=8.3)
        for label, lid in self.nav_items:
            lab = _clean(label)
            lab = lab if len(lab) <= 26 else lab[:25] + "…"
            self.set_xy(nx, ny)
            self.set_text_color(*TEXT_MID)
            self.cell(SIDEBAR_W, 5.5, lab)
            self.link(nx, ny, SIDEBAR_W, 5.5, lid)
            ny += 7
        self.set_draw_color(*DIVIDER)
        self.line(MARGIN + SIDEBAR_W + 1, 24, MARGIN + SIDEBAR_W + 1, PAGE_H - FOOTER_SPACE)

    def footer(self):
        self.set_y(-12)
        self.set_draw_color(*DIVIDER)
        self.line(self.cx, self.get_y(), PAGE_W - MARGIN, self.get_y())
        self.set_font("Reg", size=7.5); self.set_text_color(*TEXT_MID)
        self.set_x(self.cx)
        self.cell(self.cw / 2, 7, f"Сформировано: {self.gen_str}")
        self.cell(self.cw / 2, 7, f"стр. {self.page_no()}", align="R")


def _fit(pdf, text, width) -> str:
    """Обрезает текст с многоточием, чтобы влезал в заданную ширину (мм)."""
    text = _clean(text)
    if pdf.get_string_width(text) <= width:
        return text
    while text and pdf.get_string_width(text + "…") > width:
        text = text[:-1]
    return (text + "…") if text else ""


def _kv_row(pdf, cx, cy, key, val, shade) -> float:
    pdf.set_xy(cx, cy)
    pdf.set_fill_color(*(ROW_SHADE if shade else CARD_BG))
    pdf.set_font("Reg", size=9); pdf.set_text_color(*TEXT_LABEL)
    pdf.cell(KEY_W, 7, _fit(pdf, key, KEY_W - 2), fill=True)
    pdf.set_font("Reg", size=9.5); pdf.set_text_color(*TEXT_DARK)
    vw = pdf.inner_w - KEY_W
    pdf.cell(vw, 7, _fit(pdf, val, vw - 2), fill=True)
    return cy + 7


def _render_hero(pdf, title, subtitle, summary_pairs, hero_image=None,
                 summary_label="Краткая сводка", link_id=None):
    name = _clean(title)
    sub  = _clean(subtitle)
    rows = summary_pairs or []

    img_box = 24 if hero_image and _img_meta(hero_image) else 0
    text_top = 0
    h = CARD_PAD * 2 + 12 + (7 if sub else 0)
    if rows:
        h += 4 + 9 + 3 + len(rows) * 7
    h = max(h, CARD_PAD * 2 + img_box) if img_box else h

    x, y = pdf.cx, pdf.get_y()
    if link_id:
        pdf.set_link(link_id, y=max(y - 3, 0), page=pdf.page_no())
    pdf.set_draw_color(*CARD_BORDER); pdf.set_fill_color(*CARD_BG)
    pdf.rect(x, y, pdf.cw, h, style="FD")

    cx, cy = x + CARD_PAD, y + CARD_PAD
    if img_box:
        meta = _img_meta(hero_image)
        pdf.set_fill_color(*ROW_SHADE)
        pdf.rect(cx, cy, img_box, img_box, style="F")
        # вписываем по меньшей стороне
        iw, ih = meta[1], meta[2]
        scale = min(img_box / iw, img_box / ih)
        dw, dh = iw * scale, ih * scale
        pdf.image(meta[0], x=cx + (img_box - dw) / 2, y=cy + (img_box - dh) / 2, w=dw, h=dh)
        cx += img_box + 5

    pdf.set_xy(cx, cy)
    pdf.set_font("Bold", size=20); pdf.set_text_color(*TEXT_DARK)
    avail = pdf.cw - (cx - x) - CARD_PAD
    pdf.cell(avail, 11, _fit(pdf, name, avail))
    cy += 12
    if sub:
        pdf.set_xy(cx, cy); pdf.set_font("Reg", size=10.5); pdf.set_text_color(*TEXT_MID)
        pdf.cell(avail, 6, _fit(pdf, sub, avail)); cy += 7
    if rows:
        cy += 4
        pdf.set_xy(cx, cy); pdf.set_font("Bold", size=11); pdf.set_text_color(*TEXT_DARK)
        pdf.cell(60, 7, _clean(summary_label)); cy += 9
        pdf.set_draw_color(*DIVIDER); pdf.line(cx, cy, x + pdf.cw - CARD_PAD, cy); cy += 3
        # сводка рисуется на всю ширину карточки (под логотипом)
        kx = x + CARD_PAD
        shade = False
        for k, v in rows:
            _kv_row(pdf, kx, cy, k, v, shade)
            cy += 7
            shade = not shade
    pdf.set_y(y + h + 4)


def _section_height(pdf, sec) -> float:
    h = 0.0
    if sec.get("title"):
        h += 9 + 4
    if sec.get("pairs"):
        h += len(sec["pairs"]) * 7 + 2
    if sec.get("tags"):
        h += _tags_height(pdf, [_clean(t) for t in sec["tags"]], pdf.inner_w) + 2
    if sec.get("image"):
        meta = _img_meta(sec["image"])
        if meta:
            dw = min(pdf.inner_w, 150)
            dh = dw * meta[2] / meta[1]
            if dh > 95:
                dh = 95
            sec["_meta"], sec["_dh"] = meta, dh
            h += dh + 2 + (5 if sec.get("caption") else 0)
    if sec.get("note"):
        pdf.set_font("Reg", size=9)
        h += len(_wrap_lines(pdf, _clean(sec["note"]), pdf.inner_w)) * 5 + 2
    return h


def _render_section(pdf, sec, link_id=None):
    content_h = _section_height(pdf, sec)
    card_h = CARD_PAD * 2 + content_h
    if pdf.get_y() + card_h > PAGE_H - FOOTER_SPACE:
        pdf.add_page()

    x, y = pdf.cx, pdf.get_y()
    if link_id:
        pdf.set_link(link_id, y=max(y - 3, 0), page=pdf.page_no())
    pdf.set_draw_color(*CARD_BORDER); pdf.set_fill_color(*CARD_BG)
    pdf.rect(x, y, pdf.cw, card_h, style="FD")

    cx, cy = x + CARD_PAD, y + CARD_PAD
    if sec.get("title"):
        pdf.set_xy(cx, cy)
        pdf.set_font("Bold", size=12.5); pdf.set_text_color(*TEXT_DARK)
        t = _clean(sec["title"])
        pdf.cell(pdf.get_string_width(t) + 2, 8, t)
        if sec.get("subtitle"):
            pdf.set_font("Reg", size=9.5); pdf.set_text_color(*TEXT_LABEL)
            pdf.cell(50, 8, " " + _clean(sec["subtitle"]))
        cy += 9
        pdf.set_draw_color(*DIVIDER); pdf.line(cx, cy, x + pdf.cw - CARD_PAD, cy); cy += 4

    if sec.get("pairs"):
        shade = False
        for k, v in sec["pairs"]:
            _kv_row(pdf, cx, cy, k, v, shade); cy += 7; shade = not shade
        cy += 2

    if sec.get("tags"):
        cy = _draw_tags(pdf, cx, cy, [_clean(t) for t in sec["tags"]], pdf.inner_w) + 2

    if sec.get("image") and sec.get("_meta"):
        meta, dh = sec["_meta"], sec["_dh"]
        dw = dh * meta[1] / meta[2]
        if dw > pdf.inner_w:
            dw = pdf.inner_w; dh = dw * meta[2] / meta[1]
        ix = x + (pdf.cw - dw) / 2
        pdf.image(meta[0], x=ix, y=cy, w=dw, h=dh)
        cy += dh + 2
        if sec.get("caption"):
            pdf.set_xy(cx, cy); pdf.set_font("Reg", size=8); pdf.set_text_color(*TEXT_MID)
            pdf.cell(pdf.inner_w, 4.5, _clean(sec["caption"]), align="C"); cy += 5

    if sec.get("note"):
        pdf.set_font("Reg", size=9); pdf.set_text_color(*TEXT_MID)
        for line in _wrap_lines(pdf, _clean(sec["note"]), pdf.inner_w):
            pdf.set_xy(cx, cy); pdf.cell(pdf.inner_w, 5, line); cy += 5
        cy += 2

    pdf.set_y(y + card_h + 4)


def generate_report_pdf(
    title: str,
    subtitle: str = "",
    summary_pairs: Optional[List[Tuple[str, str]]] = None,
    sections: Optional[List[Dict]] = None,
    brand: Tuple[str, str] = ("Бизнес", "отчёт"),
    accent: Tuple[int, int, int] = ACCENT_DEF,
    logo_path: Optional[str] = None,
    hero_image=None,
    nav: bool = False,
    disclaimer: Optional[str] = None,
) -> bytes:
    """
    nav         — боковая навигация с кликабельными ссылками по разделам.
    hero_image  — логотип/аватар (bytes или путь) в карточке-шапке.
    sections    — секции; помимо pairs/tags/note секция может содержать
                  "image": bytes|path и "caption": "...".
    logo_path   — логотип в шапке листа (вместо точки).
    """
    if not (os.path.exists(FONT_PATH) and os.path.exists(FONT_BOLD_PATH)):
        raise FileNotFoundError("Нет шрифтов DejaVu — вызови ensure_fonts() перед генерацией.")

    sections = sections or []
    pdf = ReportPDF(brand_dark=brand[0], brand_light=brand[1], accent=accent,
                    logo_path=logo_path, nav=nav)
    pdf.add_font("Reg",  fname=FONT_PATH)
    pdf.add_font("Bold", fname=FONT_BOLD_PATH)
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    # навигация: ссылки создаём после первой страницы и сразу даём им
    # страницу-заглушку, иначе fpdf не даст повесить кликабельную область
    hero_link = None
    sec_links = {}
    if nav:
        hero_link = pdf.add_link(); pdf.set_link(hero_link, page=1, y=0)
        pdf.nav_items.append(("Сводка", hero_link))
        for i, sec in enumerate(sections):
            if sec.get("title"):
                lid = pdf.add_link(); pdf.set_link(lid, page=1, y=0)
                sec_links[i] = lid
                pdf.nav_items.append((sec["title"], lid))
        pdf._draw_sidebar()          # навигация на стр. 1 (на след. страницах — в header)
        pdf.set_y(22)

    _render_hero(pdf, title, subtitle, summary_pairs or [], hero_image=hero_image, link_id=hero_link)
    for i, sec in enumerate(sections):
        _render_section(pdf, sec, link_id=sec_links.get(i))

    if disclaimer:
        if pdf.get_y() > PAGE_H - FOOTER_SPACE - 14:
            pdf.add_page()
        pdf.set_x(pdf.cx); pdf.set_font("Reg", size=7.5); pdf.set_text_color(*TEXT_MID)
        pdf.multi_cell(pdf.cw, 4.2, _clean(disclaimer), align="C")

    return bytes(pdf.output())
