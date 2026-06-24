"""
report_pdf.py — чистый PDF-отчёт в светлом «report»-стиле.

Назначение: ЛЕГАЛЬНЫЕ данные о ЮРИДИЧЕСКИХ лицах и инфраструктуре —
  • компании по ИНН/ОГРН (открытый реестр ЕГРЮЛ/ФНС, в т.ч. через DaData);
  • домены и IP (WHOIS, гео, ASN).

НЕ предназначен для сбора досье на частных лиц
(телефон→личность, ник→кросс-платформенный профиль и т.п.).

Зависимости: fpdf2, шрифты DejaVu в /tmp (см. ensure_fonts() в основном боте).
"""

import os
import re
from datetime import datetime
from typing import Optional, Sequence, Tuple, List, Dict

from fpdf import FPDF, XPos, YPos

# ─── Шрифты (кириллица) ────────────────────────────────────────────────
FONT_PATH      = "/tmp/DejaVuSans.ttf"
FONT_BOLD_PATH = "/tmp/DejaVuSans-Bold.ttf"

# ─── Палитра (светлая тема) ────────────────────────────────────────────
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
ACCENT_DEF  = (30,  41,  59)   # тёмный «slate», можно переопределить

# ─── Геометрия (A4) ────────────────────────────────────────────────────
PAGE_W, PAGE_H = 210, 297
MARGIN         = 14
CONTENT_W      = PAGE_W - 2 * MARGIN       # 182
CARD_PAD       = 6
INNER_W        = CONTENT_W - 2 * CARD_PAD  # 170
FOOTER_SPACE   = 16
KEY_W          = 52                        # ширина колонки-ключа


def _clean(text) -> str:
    """Снимает Markdown и эмодзи — DejaVu их не рендерит."""
    if text is None:
        return ""
    text = str(text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)   # [текст](url) → текст
    text = re.sub(r'[*_`#]', '', text)
    text = re.sub(
        r'[\U0001F000-\U0001FAFF\U00002600-\U000027BF'
        r'\U0001F900-\U0001F9FF\u200d\ufe0f\u20e3]+',
        '', text, flags=re.UNICODE
    )
    return re.sub(r'  +', ' ', text).strip()


def _wrap_lines(pdf: FPDF, text: str, width: float) -> List[str]:
    """Жадно переносит текст по ширине (для примечаний)."""
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


# ─── Теги-чипы ─────────────────────────────────────────────────────────
_TAG_H, _TAG_GAP_X, _TAG_GAP_Y = 8.0, 3.0, 2.0


def _tags_height(pdf: FPDF, items: Sequence[str], max_w: float) -> float:
    pdf.set_font("Reg", size=9)
    x, lines = 0.0, 1
    for it in items:
        tw = pdf.get_string_width(it) + 6
        if x + tw > max_w and x > 0:
            x = 0
            lines += 1
        x += tw + _TAG_GAP_X
    return lines * _TAG_H + (lines - 1) * _TAG_GAP_Y


def _draw_tags(pdf: FPDF, x0: float, y0: float, items: Sequence[str], max_w: float) -> float:
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


# ─── Класс PDF с хедером/футером ───────────────────────────────────────
class ReportPDF(FPDF):
    def __init__(self, brand_dark="Бизнес", brand_light="отчёт", accent=ACCENT_DEF,
                 logo_path=None, **kw):
        super().__init__(**kw)
        self.brand_dark  = brand_dark
        self.brand_light = brand_light
        self.accent      = accent
        self.logo_path   = logo_path if (logo_path and os.path.exists(logo_path)) else None
        self.gen_str     = datetime.now().strftime("%d.%m.%Y %H:%M")

    def header(self):
        self.set_fill_color(*PAGE_BG)
        self.rect(0, 0, PAGE_W, PAGE_H, style="F")        # фон страницы

        if self.logo_path:                                # свой логотип-картинка
            lh = 8.0
            try:
                from PIL import Image
                with Image.open(self.logo_path) as im:
                    lw = min(lh * im.width / im.height, 60)
            except Exception:
                lw = lh
            self.image(self.logo_path, x=MARGIN, y=8.5, h=lh)
            tx = MARGIN + lw + 3
        else:                                             # дефолт: точка-кружок
            self.set_fill_color(*self.accent)
            self.ellipse(MARGIN, 10.3, 4.2, 4.2, style="F")
            tx = MARGIN + 6.5

        if self.brand_dark or self.brand_light:           # текст бренда (опционально)
            self.set_xy(tx, 9)
            self.set_font("Bold", size=12)
            self.set_text_color(*TEXT_DARK)
            if self.brand_dark:
                self.cell(self.get_string_width(self.brand_dark) + 1, 7, self.brand_dark)
            self.set_font("Reg", size=12)
            self.set_text_color(*TEXT_MID)
            if self.brand_light:
                self.cell(40, 7, (" " if self.brand_dark else "") + self.brand_light)

        self.set_draw_color(*DIVIDER)
        self.line(MARGIN, 18, PAGE_W - MARGIN, 18)        # тонкая линия под шапкой
        self.set_y(22)                                    # курсор ниже логотипа

    def footer(self):
        self.set_y(-12)
        self.set_draw_color(*DIVIDER)
        self.line(MARGIN, self.get_y(), PAGE_W - MARGIN, self.get_y())
        self.set_font("Reg", size=7.5)
        self.set_text_color(*TEXT_MID)
        self.cell(CONTENT_W / 2, 7, f"Сформировано: {self.gen_str}")
        self.cell(CONTENT_W / 2, 7, f"стр. {self.page_no()}", align="R")


# ─── Рендер блоков ─────────────────────────────────────────────────────
def _kv_row(pdf: FPDF, cx: float, cy: float, key: str, val: str, shade: bool) -> float:
    pdf.set_xy(cx, cy)
    pdf.set_fill_color(*(ROW_SHADE if shade else CARD_BG))
    pdf.set_font("Reg", size=9)
    pdf.set_text_color(*TEXT_LABEL)
    pdf.cell(KEY_W, 7, _clean(key)[:34], fill=True)
    pdf.set_font("Reg", size=9.5)
    pdf.set_text_color(*TEXT_DARK)
    pdf.cell(INNER_W - KEY_W, 7, _clean(val)[:78], fill=True)
    return cy + 7


def _render_hero(pdf: FPDF, title: str, subtitle: str,
                 summary_pairs: List[Tuple[str, str]], summary_label="Краткая сводка"):
    name = _clean(title)
    sub  = _clean(subtitle)
    rows = summary_pairs or []

    h = CARD_PAD * 2 + 12 + (7 if sub else 0)
    if rows:
        h += 4 + 9 + 3 + len(rows) * 7

    x, y = MARGIN, pdf.get_y()
    pdf.set_draw_color(*CARD_BORDER)
    pdf.set_fill_color(*CARD_BG)
    pdf.rect(x, y, CONTENT_W, h, style="FD")

    cx, cy = x + CARD_PAD, y + CARD_PAD
    pdf.set_xy(cx, cy)
    pdf.set_font("Bold", size=20)
    pdf.set_text_color(*TEXT_DARK)
    pdf.cell(INNER_W, 11, name[:40])
    cy += 12
    if sub:
        pdf.set_xy(cx, cy)
        pdf.set_font("Reg", size=10.5)
        pdf.set_text_color(*TEXT_MID)
        pdf.cell(INNER_W, 6, sub[:80])
        cy += 7
    if rows:
        cy += 4
        pdf.set_xy(cx, cy)
        pdf.set_font("Bold", size=11)
        pdf.set_text_color(*TEXT_DARK)
        pdf.cell(INNER_W, 7, _clean(summary_label))
        cy += 9
        pdf.set_draw_color(*DIVIDER)
        pdf.line(cx, cy, x + CONTENT_W - CARD_PAD, cy)
        cy += 3
        shade = False
        for k, v in rows:
            cy = _kv_row(pdf, cx, cy, k, v, shade)
            shade = not shade
    pdf.set_y(y + h + 4)


def _section_height(pdf: FPDF, sec: Dict) -> float:
    h = 0.0
    if sec.get("title"):
        h += 9 + 4
    if sec.get("pairs"):
        h += len(sec["pairs"]) * 7 + 2
    if sec.get("tags"):
        h += _tags_height(pdf, [_clean(t) for t in sec["tags"]], INNER_W) + 2
    if sec.get("note"):
        pdf.set_font("Reg", size=9)
        h += len(_wrap_lines(pdf, _clean(sec["note"]), INNER_W)) * 5 + 2
    return h


def _render_section(pdf: FPDF, sec: Dict):
    content_h = _section_height(pdf, sec)
    card_h = CARD_PAD * 2 + content_h
    if pdf.get_y() + card_h > PAGE_H - FOOTER_SPACE:
        pdf.add_page()

    x, y = MARGIN, pdf.get_y()
    pdf.set_draw_color(*CARD_BORDER)
    pdf.set_fill_color(*CARD_BG)
    pdf.rect(x, y, CONTENT_W, card_h, style="FD")

    cx, cy = x + CARD_PAD, y + CARD_PAD
    if sec.get("title"):
        pdf.set_xy(cx, cy)
        pdf.set_font("Bold", size=12.5)
        pdf.set_text_color(*TEXT_DARK)
        t = _clean(sec["title"])
        pdf.cell(pdf.get_string_width(t) + 2, 8, t)
        if sec.get("subtitle"):
            pdf.set_font("Reg", size=9.5)
            pdf.set_text_color(*TEXT_LABEL)
            pdf.cell(50, 8, " " + _clean(sec["subtitle"]))
        cy += 9
        pdf.set_draw_color(*DIVIDER)
        pdf.line(cx, cy, x + CONTENT_W - CARD_PAD, cy)
        cy += 4

    if sec.get("pairs"):
        shade = False
        for k, v in sec["pairs"]:
            cy = _kv_row(pdf, cx, cy, k, v, shade)
            shade = not shade
        cy += 2

    if sec.get("tags"):
        cy = _draw_tags(pdf, cx, cy, [_clean(t) for t in sec["tags"]], INNER_W) + 2

    if sec.get("note"):
        pdf.set_font("Reg", size=9)
        pdf.set_text_color(*TEXT_MID)
        for line in _wrap_lines(pdf, _clean(sec["note"]), INNER_W):
            pdf.set_xy(cx, cy)
            pdf.cell(INNER_W, 5, line)
            cy += 5
        cy += 2

    pdf.set_y(y + card_h + 4)


# ─── Точка входа ───────────────────────────────────────────────────────
def generate_report_pdf(
    title: str,
    subtitle: str = "",
    summary_pairs: Optional[List[Tuple[str, str]]] = None,
    sections: Optional[List[Dict]] = None,
    brand: Tuple[str, str] = ("Бизнес", "отчёт"),
    accent: Tuple[int, int, int] = ACCENT_DEF,
    logo_path: Optional[str] = None,
    disclaimer: Optional[str] = None,
) -> bytes:
    """
    Собирает PDF в светлом стиле.

    title           — крупный заголовок (название компании / домен).
    subtitle        — серая строка под ним (ИНН · статус / тип объекта).
    summary_pairs   — пары (ключ, значение) для блока «Краткая сводка».
    sections        — список секций-карточек. Каждая секция — dict:
        {
          "title": "...", "subtitle": "..." (опц.),
          "pairs": [("Ключ","Значение"), ...],   # таблица
          "tags":  ["ОКВЭД 62.01", ...],         # чипы
          "note":  "произвольный текст",          # абзац
        }
        В одной секции можно сочетать pairs / tags / note.
    brand           — (тёмная часть лого, серая часть лого). Можно ("", "") — без текста.
    accent          — цвет точки-логотипа (если logo_path не задан).
    logo_path       — путь к PNG/JPG логотипу; заменяет точку-кружок.
    disclaimer      — мелкий текст внизу (источник данных, оговорки).
    """
    if not (os.path.exists(FONT_PATH) and os.path.exists(FONT_BOLD_PATH)):
        raise FileNotFoundError("Нет шрифтов DejaVu — вызови ensure_fonts() перед генерацией.")

    pdf = ReportPDF(brand_dark=brand[0], brand_light=brand[1], accent=accent,
                    logo_path=logo_path)
    pdf.add_font("Reg",  fname=FONT_PATH)
    pdf.add_font("Bold", fname=FONT_BOLD_PATH)
    pdf.set_margins(MARGIN, 22, MARGIN)
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    _render_hero(pdf, title, subtitle, summary_pairs or [])
    for sec in (sections or []):
        _render_section(pdf, sec)

    if disclaimer:
        if pdf.get_y() > PAGE_H - FOOTER_SPACE - 14:
            pdf.add_page()
        pdf.set_x(MARGIN)
        pdf.set_font("Reg", size=7.5)
        pdf.set_text_color(*TEXT_MID)
        pdf.multi_cell(CONTENT_W, 4.2, _clean(disclaimer), align="C")

    return bytes(pdf.output())
