#!/usr/bin/env python3
"""ESTÜ yemekhane menülerini toplayıp data/menu.json üretir.

Betik yalnızca Python standart kütüphanesini kullanır. PDF ayrıştırıcısı, ESTÜ
PDF'lerinde kullanılan FlateDecode akışlarını, Tj/TJ metin operatörlerini ve
ToUnicode CMap eşlemelerini çözer. PDF dosyaları URL özetiyle
 data/cache/pdfs/ altında saklanır; aynı URL tekrar seçildiğinde ESTÜ'den PDF
indirimi yapılmaz.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import math
import re
import sys
import zlib
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


ESTU_HOST = "saglikkulturspor.eskisehir.edu.tr"
ESTU_BASE = f"https://{ESTU_HOST}"
MAIN_PAGE_URL = f"{ESTU_BASE}/tr/Icerik/Detay/yemekhaneler"
CLUB_PAGE_URL = f"{ESTU_BASE}/tr/Icerik/Detay/gunluk-menu"
TIMEZONE = ZoneInfo("Europe/Istanbul")
USER_AGENT = "ESTU-menu-github-actions/1.0 (+https://github.com/beytullahgol/estu-menu)"

MONTH_NAMES = {
    1: ("Ocak", "OCAK"),
    2: ("Şubat", "Subat", "ŞUBAT"),
    3: ("Mart", "MART"),
    4: ("Nisan", "NISAN"),
    5: ("Mayıs", "Mayis", "MAYIS"),
    6: ("Haziran", "HAZİRAN"),
    7: ("Temmuz", "TEMMUZ"),
    8: ("Ağustos", "Agustos", "AĞUSTOS"),
    9: ("Eylül", "Eylul", "EYLÜL"),
    10: ("Ekim", "EKİM"),
    11: ("Kasım", "Kasim", "KASIM"),
    12: ("Aralık", "Aralik", "ARALIK"),
}


@dataclass
class TextBlock:
    index: int
    text: str
    x: float
    y: float
    group_x: float | None


@dataclass
class Link:
    url: str
    label: str


class AnchorParser(HTMLParser):
    """HTML sayfasındaki a[href] etiketlerini bağımlılıksız toplar."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._current_href is not None:
            return
        attributes = dict(attrs)
        href = attributes.get("href")
        self._current_href = href or ""
        self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._current_href is not None:
            self.links.append((self._current_href, " ".join(self._current_text)))
            self._current_href = None
            self._current_text = []


def normalize_spaces(value: str) -> str:
    value = html_lib.unescape(value).replace("\xa0", " ")
    value = value.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", value).strip()


def is_allowed_estu_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "").lower() == ESTU_HOST


def resolve_estu_url(href: str, page_url: str) -> str | None:
    href = html_lib.unescape(href.strip())
    if not href:
        return None
    resolved = urljoin(page_url, href)
    return resolved if is_allowed_estu_url(resolved) else None


def extract_pdf_links(page_html: bytes, page_url: str) -> list[Link]:
    parser = AnchorParser()
    parser.feed(page_html.decode("utf-8", errors="replace"))
    links: list[Link] = []
    for href, label in parser.links:
        url = resolve_estu_url(href, page_url)
        if url is None or re.search(r"\.pdf(?:[?#]|$)", url, re.IGNORECASE) is None:
            continue
        links.append(Link(url=url, label=normalize_spaces(label)))
    return links


def select_monthly_pdf(links: Iterable[Link], target: datetime) -> Link | None:
    month_variants = MONTH_NAMES[target.month]
    year = str(target.year)
    fallback: Link | None = None
    for link in links:
        label = link.label
        decoded_url = unquote(link.url)
        label_folded = label.casefold()
        url_folded = decoded_url.casefold()
        is_academic = "akademik" in label_folded or "akademik" in url_folded
        is_weekly = "haftalık" in label_folded or "haftalik" in label_folded or "haftalik_menu_" in url_folded
        has_menu_marker = (
            "yemek menüsü" in label_folded
            or "yemek menusu" in label_folded
            or "menü" in url_folded
            or "menu" in url_folded
        )
        if is_academic or is_weekly or not has_menu_marker:
            continue
        if fallback is None:
            fallback = link
        if any(
            re.search(re.escape(month) + r"[\s-]*" + re.escape(year), label, re.IGNORECASE)
            or re.search(re.escape(month) + r"[\s-]*" + re.escape(year), decoded_url, re.IGNORECASE)
            for month in month_variants
        ):
            return link
    return fallback


def select_weekly_pdf(links: Iterable[Link]) -> Link | None:
    for link in links:
        label_folded = link.label.casefold()
        url_folded = unquote(link.url).casefold()
        if (
            "haftalık" in label_folded
            or "haftalik" in label_folded
            or "/haftalik_menu_" in url_folded
        ):
            return link
    return None


OBJECT_RE = re.compile(rb"(?:^|\n)(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj\b", re.DOTALL)


def pdf_object_map(pdf_data: bytes) -> dict[int, bytes]:
    return {int(match.group(1)): match.group(3) for match in OBJECT_RE.finditer(pdf_data)}


def pdf_stream_data(obj: bytes) -> bytes:
    stream_position = obj.find(b"stream")
    if stream_position < 0:
        return obj
    data = obj[stream_position + len(b"stream") :]
    if data.startswith(b"\r\n"):
        data = data[2:]
    elif data.startswith((b"\n", b"\r")):
        data = data[1:]
    end_position = data.find(b"endstream")
    if end_position >= 0:
        data = data[:end_position]
    data = data.rstrip(b"\r\n")
    if b"/FlateDecode" in obj[:stream_position]:
        try:
            return zlib.decompress(data)
        except zlib.error:
            try:
                return zlib.decompress(data, -15)
            except zlib.error:
                pass
    return data


def unicode_codepoint_to_text(codepoint: int) -> str:
    try:
        return chr(codepoint)
    except ValueError:
        return ""


def parse_tounicode_cmap(cmap: bytes) -> dict[int, str]:
    text = cmap.decode("latin1", errors="ignore")
    mapping: dict[int, str] = {}

    for section in re.findall(r"beginbfchar(.*?)endbfchar", text, re.IGNORECASE | re.DOTALL):
        for source, destination in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", section):
            mapping[int(source, 16)] = unicode_codepoint_to_text(int(destination, 16))

    for section in re.findall(r"beginbfrange(.*?)endbfrange", text, re.IGNORECASE | re.DOTALL):
        for array_match in re.finditer(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]",
            section,
            re.DOTALL,
        ):
            first = int(array_match.group(1), 16)
            last = int(array_match.group(2), 16)
            destinations = re.findall(r"<([0-9A-Fa-f]+)>", array_match.group(3))
            for offset, destination in enumerate(destinations):
                source = first + offset
                if source > last:
                    break
                mapping[source] = unicode_codepoint_to_text(int(destination, 16))

        section_without_arrays = re.sub(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[.*?\]",
            "",
            section,
            flags=re.DOTALL,
        )
        for source, last, destination in re.findall(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
            section_without_arrays,
        ):
            first_value = int(source, 16)
            last_value = int(last, 16)
            destination_value = int(destination, 16)
            for current in range(first_value, last_value + 1):
                mapping[current] = unicode_codepoint_to_text(
                    destination_value + current - first_value
                )
    return mapping


def parse_literal_pdf_string(value: bytes) -> str:
    output = bytearray()
    simple = {
        ord("n"): b"\n",
        ord("r"): b"\r",
        ord("t"): b"\t",
        ord("b"): b"\x08",
        ord("f"): b"\x0c",
        ord("("): b"(",
        ord(")"): b")",
        ord("\\"): b"\\",
    }
    index = 0
    while index < len(value):
        char = value[index]
        if char != ord("\\"):
            output.append(char)
            index += 1
            continue
        index += 1
        if index >= len(value):
            break
        escaped = value[index]
        if escaped in simple:
            output.extend(simple[escaped])
            index += 1
            continue
        if ord("0") <= escaped <= ord("7"):
            octal = bytes([escaped])
            for _ in range(2):
                if index + 1 >= len(value) or not (ord("0") <= value[index + 1] <= ord("7")):
                    break
                index += 1
                octal += bytes([value[index]])
            output.append(int(octal, 8))
            index += 1
            continue
        if escaped in (ord("\r"), ord("\n")):
            if escaped == ord("\r") and index + 1 < len(value) and value[index + 1] == ord("\n"):
                index += 1
            index += 1
            continue
        output.append(escaped)
        index += 1
    return bytes(output).decode("cp1252", errors="ignore")


def decode_pdf_hex_string(value: bytes, mapping: dict[int, str]) -> str:
    value = re.sub(rb"\s+", b"", value)
    if not value:
        return ""
    if len(value) % 2:
        value += b"0"
    try:
        raw = bytes.fromhex(value.decode("ascii"))
    except ValueError:
        return ""
    output: list[str] = []
    for index in range(0, len(raw) - 1, 2):
        source = (raw[index] << 8) | raw[index + 1]
        if source in mapping:
            output.append(mapping[source])
    return "".join(output)


def decode_pdf_text_show(show: bytes, mapping: dict[int, str]) -> str:
    output: list[str] = []
    token_re = re.compile(rb"\((?:\\.|[^\\)])*\)|<([0-9A-Fa-f\s]+)>", re.DOTALL)
    for token_match in token_re.finditer(show):
        token = token_match.group(0)
        if token.startswith(b"("):
            output.append(parse_literal_pdf_string(token[1:-1]))
        else:
            output.append(decode_pdf_hex_string(token[1:-1], mapping))
    return "".join(output)


def pdf_text_blocks(pdf_data: bytes) -> list[TextBlock]:
    objects = pdf_object_map(pdf_data)
    page_object: bytes | None = None
    for obj in objects.values():
        if re.search(rb"/Type\s*/Page\b", obj) and re.search(rb"/Contents\s+\d+\s+0\s+R", obj):
            page_object = obj
            break
    if page_object is None:
        return []

    contents_match = re.search(rb"/Contents\s+(\d+)\s+0\s+R", page_object)
    if contents_match is None:
        return []
    content_object = objects.get(int(contents_match.group(1)))
    if content_object is None:
        return []
    content = pdf_stream_data(content_object)

    font_maps: dict[bytes, dict[int, str]] = {}
    for font_match in re.finditer(rb"/(F\d+)\s+(\d+)\s+0\s+R", page_object):
        font_name = font_match.group(1)
        font_object = objects.get(int(font_match.group(2)), b"")
        mapping: dict[int, str] = {}
        unicode_match = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", font_object)
        if unicode_match is not None:
            unicode_object = objects.get(int(unicode_match.group(1)), b"")
            if unicode_object:
                mapping = parse_tounicode_cmap(pdf_stream_data(unicode_object))
        font_maps[font_name] = mapping

    blocks: list[TextBlock] = []
    bt_re = re.compile(rb"BT(.*?)ET", re.DOTALL)
    tm_re = re.compile(
        rb"(?:1\s+0\s+0\s+1|1\s+0\s+0\s+-1)\s+"
        rb"([+-]?(?:\d+\.?\d*|\.\d+))\s+"
        rb"([+-]?(?:\d+\.?\d*|\.\d+))\s+Tm",
        re.DOTALL,
    )
    tf_re = re.compile(rb"/(F\d+)\s+[0-9.+-]+\s+Tf")
    show_re = re.compile(
        rb"(\[.*?\]\s*TJ|\((?:\\.|[^\\)])*\)\s*Tj|<[^>]+>\s*Tj)",
        re.DOTALL,
    )
    group_re = re.compile(
        rb"4(?:\.0+)?\s+0\s+0\s+4(?:\.0+)?\s+"
        rb"([+-]?(?:\d+\.?\d*|\.\d+))\s+"
        rb"[+-]?(?:\d+\.?\d*|\.\d+)\s+cm"
    )

    for bt_match in bt_re.finditer(content):
        body = bt_match.group(1)
        font_match = tf_re.search(body)
        tm_match = tm_re.search(body)
        if font_match is None or tm_match is None:
            continue

        text_parts: list[str] = []
        for show_match in show_re.finditer(body):
            show = show_match.group(1).strip()
            if show.endswith(b"TJ"):
                show = show[:-2].strip()
            if show.startswith(b"[") and show.endswith(b"]"):
                show = show[1:-1]
            text_parts.append(decode_pdf_text_show(show, font_maps.get(font_match.group(1), {})))

        prefix = content[: bt_match.start(1)]
        group_matches = list(group_re.finditer(prefix))
        group_x = float(group_matches[-1].group(1)) if group_matches else None
        blocks.append(
            TextBlock(
                index=len(blocks),
                text=normalize_spaces("".join(text_parts)),
                x=float(tm_match.group(1)),
                y=float(tm_match.group(2)),
                group_x=group_x,
            )
        )
    return blocks


def remove_duplicate_items(items: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        item = normalize_spaces(item)
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def is_monthly_non_menu_text(text: str) -> bool:
    if not text:
        return True
    if re.fullmatch(r"\d+(?:[.,]\d+)?(?:\s*(?:gr\.?|ml\.?))?", text, re.IGNORECASE):
        return True
    return re.fullmatch(r"(?:Miktar|Kalori|gr\.?|ml\.?)", text, re.IGNORECASE) is not None


def extract_monthly_items(blocks: list[TextBlock], date_text: str) -> list[str]:
    target = next(
        (block for block in blocks if re.search(r"(?<!\d)" + re.escape(date_text) + r"(?!\d)", block.text)),
        None,
    )
    if target is None:
        return []

    next_date_y = -math.inf
    for block in blocks:
        if block.y < target.y and re.search(r"\d{2}\.\d{2}\.\d{4}", block.text):
            next_date_y = max(next_date_y, block.y)

    items: list[str] = []
    for block in blocks:
        if block.x >= target.x or block.x < target.x - 95:
            continue
        if block.y >= target.y - 0.1 or block.y <= next_date_y + 0.2:
            continue
        if is_monthly_non_menu_text(block.text) or re.search(r"\d{2}\.\d{2}\.\d{4}", block.text):
            continue
        items.append(block.text)
    return remove_duplicate_items(items)


def is_weekly_non_menu_text(text: str) -> bool:
    if not text:
        return True
    if re.search(r"\d{1,2}\.\d{1,2}\.\d{4}|Pazartesi|Salı|Çarşamba|Perşembe|Cuma|Cumartesi|Pazar", text, re.IGNORECASE):
        return True
    if re.search(r"kcal|₺|TL|SERVİS|SOĞUKLAR|MİKTAR|KALORİ", text, re.IGNORECASE):
        return True
    return re.fullmatch(r"[0-9.,+\- ]+", text) is not None


def extract_weekly_items(blocks: list[TextBlock], date_text: str) -> list[str]:
    target = next(
        (block for block in blocks if re.search(r"(?<!\d)" + re.escape(date_text) + r"(?!\d)", block.text)),
        None,
    )
    if target is None or target.group_x is None:
        return []

    items: list[str] = []
    for block in blocks:
        if block.index <= target.index or block.group_x is None:
            continue
        if abs(block.group_x - target.group_x) > 0.5:
            continue
        if is_weekly_non_menu_text(block.text):
            continue
        if block.x < -1:
            # PDF hücre içinde satır taşan devam metnini (ör. ÇORBASI veya PİLAVI)
            # ayrı yemek değil, önceki öğenin devamı olarak yazabilir.
            if items:
                items[-1] = normalize_spaces(f"{items[-1]} {block.text}")
            continue
        items.append(block.text)
    return remove_duplicate_items(items)


def request_bytes(url: str) -> tuple[bytes, str]:
    if not is_allowed_estu_url(url):
        raise ValueError(f"Engellenen ESTÜ dışı URL: {url}")
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.5",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            final_url = response.geturl()
            if not is_allowed_estu_url(final_url):
                raise ValueError(f"Yönlendirme ESTÜ dışına çıktı: {final_url}")
            return response.read(), final_url
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"İstek başarısız ({url}): {exc}") from exc


def url_cache_path(root: Path, kind: str, url: str, suffix: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    directory = root / "data" / "cache" / kind
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{digest}{suffix}"


def get_cached_or_fetch(root: Path, url: str, kind: str, suffix: str) -> tuple[bytes, bool, str]:
    """Sayfayı güncel tutmaya çalışır; ağ hatasında son cache'i kullanır."""
    cache_path = url_cache_path(root, kind, url, suffix)
    try:
        body, final_url = request_bytes(url)
        cache_path.write_bytes(body)
        return body, False, final_url
    except Exception:
        if cache_path.exists():
            return cache_path.read_bytes(), True, url
        raise


def get_pdf_cached_or_fetch(root: Path, url: str) -> tuple[bytes, bool, str]:
    """Aynı PDF URL'sinde ESTÜ'ye tekrar PDF isteği göndermez."""
    cache_path = url_cache_path(root, "pdfs", url, ".pdf")
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return cache_path.read_bytes(), True, url
    body, final_url = request_bytes(url)
    cache_path.write_bytes(body)
    return body, False, final_url


REFRESH_TIMES = [
    f"{hour:02d}:{minute:02d}"
    for hour in range(8, 17)
    for minute in (3, 33)
    if not (hour == 16 and minute == 33)
]


def parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(TIMEZONE)
    try:
        parsed = datetime.strptime(value, "%d.%m.%Y")
    except ValueError as exc:
        raise ValueError("Tarih GG.AA.YYYY biçiminde olmalıdır.") from exc
    return parsed.replace(tzinfo=TIMEZONE)


def source_state_path(root: Path) -> Path:
    return root / "data" / "cache" / "source_state.json"


def load_source_state(root: Path) -> dict[str, dict[str, object]]:
    path = source_state_path(root)
    state: dict[str, dict[str, object]] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = {
                    str(name): dict(value)
                    for name, value in loaded.items()
                    if isinstance(value, dict)
                }
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            state = {}

    # Eski sürümün menu.json kaynak alanlarından ilk state'i oluştur.
    if not state:
        menu_path = root / "data" / "menu.json"
        try:
            current = json.loads(menu_path.read_text(encoding="utf-8"))
            sources = current.get("sources", {}) if isinstance(current, dict) else {}
            if isinstance(sources, dict):
                main_url = sources.get("anaYemekhanePdf")
                club_url = sources.get("akademikKulupPdf")
                if main_url:
                    state["anaYemekhane"] = {
                        "pageUrl": MAIN_PAGE_URL,
                        "pdfUrl": main_url,
                        "pdfLabel": sources.get("anaYemekhanePdfLinkLabel", ""),
                    }
                if club_url:
                    state["akademikKulup"] = {
                        "pageUrl": CLUB_PAGE_URL,
                        "pdfUrl": club_url,
                        "pdfLabel": sources.get("akademikKulupPdfLinkLabel", ""),
                    }
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    state.setdefault("anaYemekhane", {})
    state.setdefault("akademikKulup", {})
    return state


def save_source_state(root: Path, state: dict[str, dict[str, object]]) -> None:
    write_json(source_state_path(root), state)


def cached_pdf_path(root: Path, url: str) -> Path:
    return url_cache_path(root, "pdfs", url, ".pdf")


def cached_pdf_candidates(root: Path) -> list[Path]:
    directory = root / "data" / "cache" / "pdfs"
    return sorted(path for path in directory.glob("*.pdf") if path.is_file() and path.stat().st_size > 0)


def find_cached_items(root: Path, target: datetime, source: str) -> tuple[list[str], Path | None]:
    date_text = (
        target.strftime("%d.%m.%Y")
        if source == "main"
        else f"{target.day}.{target.month}.{target.year}"
    )
    for path in cached_pdf_candidates(root):
        try:
            blocks = pdf_text_blocks(path.read_bytes())
            items = (
                extract_monthly_items(blocks, date_text)
                if source == "main"
                else extract_weekly_items(blocks, date_text)
            )
            if items:
                return items, path
        except OSError:
            continue
    return [], None


def monthly_cache_matches_target(root: Path, target: datetime, state: dict[str, dict[str, object]]) -> bool:
    # URL/etiket yerine PDF içeriğini doğrulamak daha güvenlidir; aynı URL altında
    # dosya içeriği değişmiş olsa bile hedef gün gerçekten bulunuyorsa cache geçerlidir.
    items, _ = find_cached_items(root, target, "main")
    return bool(items)


def decide_refresh_sources(
    root: Path,
    target: datetime,
    state: dict[str, dict[str, object]],
    mode: str,
) -> set[str]:
    if mode == "cache-only" or target.isoweekday() >= 6:
        return set()
    if mode == "refresh":
        return {"main", "club"}
    if mode == "main":
        return {"main"}
    if mode == "club":
        return {"club"}
    if mode != "auto":
        raise ValueError(f"Bilinmeyen çalışma modu: {mode}")

    refresh: set[str] = set()
    first_day = target.replace(day=1)
    # Aylık PDF ayın 1'inde; ayın 1'i hafta sonuna denk gelirse ilk pazartesi
    # kontrol edilir. PDF cache'e girdikten sonra aynı gün tekrar indirme yapılmaz.
    main_due = target.day == 1 or (
        target.weekday() == 0
        and first_day.isoweekday() >= 6
        and target.day <= 7
    )
    if main_due and not monthly_cache_matches_target(root, target, state):
        refresh.add("main")
    # Akademik Kulüp PDF’i pazartesi kontrol edilir; yeni PDF cache'e girdikten
    # sonra 30 dakikalık pencerenin kalanında HTML isteği yapılmaz.
    if target.weekday() == 0:
        club_items, _ = find_cached_items(root, target, "club")
        if not club_items:
            refresh.add("club")
    return refresh


def collect_main_source(
    root: Path,
    target: datetime,
    state: dict[str, dict[str, object]],
    refresh: bool,
) -> tuple[list[str], dict[str, object]]:
    sources: dict[str, object] = {"anaYemekhanePage": MAIN_PAGE_URL}
    entry = state.setdefault("anaYemekhane", {})

    if refresh:
        main_html, page_stale, main_final_url = get_cached_or_fetch(
            root, MAIN_PAGE_URL, "pages", ".html"
        )
        main_link = select_monthly_pdf(extract_pdf_links(main_html, MAIN_PAGE_URL), target)
        if main_link is None:
            raise RuntimeError("Ana yemekhane için aylık PDF bağlantısı bulunamadı.")
        main_pdf, pdf_stale, pdf_final_url = get_pdf_cached_or_fetch(root, main_link.url)
        entry.update(
            {
                "pageUrl": MAIN_PAGE_URL,
                "pdfUrl": main_link.url,
                "pdfLabel": main_link.label,
                "finalPageUrl": main_final_url,
                "finalPdfUrl": pdf_final_url,
            }
        )
        sources.update(
            {
                "anaYemekhanePdf": main_link.url,
                "anaYemekhanePageUsedCache": page_stale,
                "anaYemekhanePdfUsedCache": pdf_stale,
                "anaYemekhaneFinalPageUrl": main_final_url,
                "anaYemekhaneFinalPdfUrl": pdf_final_url,
                "anaYemekhanePdfLinkLabel": main_link.label,
            }
        )
    else:
        pdf_url = str(entry.get("pdfUrl") or "")
        main_pdf = None
        if pdf_url:
            path = cached_pdf_path(root, pdf_url)
            if path.exists():
                main_pdf = path.read_bytes()
                sources.update(
                    {
                        "anaYemekhanePdf": pdf_url,
                        "anaYemekhanePdfUsedCache": True,
                        "anaYemekhanePageUsedCache": True,
                        "anaYemekhanePdfLinkLabel": entry.get("pdfLabel", ""),
                    }
                )
        if main_pdf is None:
            items, path = find_cached_items(root, target, "main")
            if items:
                sources.update(
                    {
                        "anaYemekhanePageUsedCache": True,
                        "anaYemekhanePdfUsedCache": True,
                        "anaYemekhanePdfCacheFile": str(path.relative_to(root)) if path else None,
                    }
                )
                return items, sources
            raise RuntimeError("Ana yemekhane yerel cache'inde hedef tarih için yemek bulunamadı.")

    items = extract_monthly_items(pdf_text_blocks(main_pdf), target.strftime("%d.%m.%Y"))
    if not items:
        # Eski source_state URL’si kalmış olabilir; yerel cache’teki diğer PDF’leri
        # ağ isteği yapmadan son kez tara.
        cached_items, cached_path = find_cached_items(root, target, "main")
        if cached_items:
            sources["anaYemekhanePdfCacheFile"] = str(cached_path.relative_to(root)) if cached_path else None
            return cached_items, sources
        raise RuntimeError("Ana yemekhane PDF'sinde hedef tarih için yemek bulunamadı.")
    return items, sources


def collect_club_source(
    root: Path,
    target: datetime,
    state: dict[str, dict[str, object]],
    refresh: bool,
) -> tuple[list[str], dict[str, object]]:
    sources: dict[str, object] = {"akademikKulupPage": CLUB_PAGE_URL}
    entry = state.setdefault("akademikKulup", {})

    if refresh:
        club_html, page_stale, club_final_url = get_cached_or_fetch(
            root, CLUB_PAGE_URL, "pages", ".html"
        )
        club_link = select_weekly_pdf(extract_pdf_links(club_html, CLUB_PAGE_URL))
        if club_link is None:
            raise RuntimeError("Akademik Kulüp için haftalık PDF bağlantısı bulunamadı.")
        club_pdf, pdf_stale, pdf_final_url = get_pdf_cached_or_fetch(root, club_link.url)
        entry.update(
            {
                "pageUrl": CLUB_PAGE_URL,
                "pdfUrl": club_link.url,
                "pdfLabel": club_link.label,
                "finalPageUrl": club_final_url,
                "finalPdfUrl": pdf_final_url,
            }
        )
        sources.update(
            {
                "akademikKulupPdf": club_link.url,
                "akademikKulupPageUsedCache": page_stale,
                "akademikKulupPdfUsedCache": pdf_stale,
                "akademikKulupFinalPageUrl": club_final_url,
                "akademikKulupFinalPdfUrl": pdf_final_url,
                "akademikKulupPdfLinkLabel": club_link.label,
            }
        )
    else:
        pdf_url = str(entry.get("pdfUrl") or "")
        club_pdf = None
        if pdf_url:
            path = cached_pdf_path(root, pdf_url)
            if path.exists():
                club_pdf = path.read_bytes()
                sources.update(
                    {
                        "akademikKulupPdf": pdf_url,
                        "akademikKulupPdfUsedCache": True,
                        "akademikKulupPageUsedCache": True,
                        "akademikKulupPdfLinkLabel": entry.get("pdfLabel", ""),
                    }
                )
        if club_pdf is None:
            items, path = find_cached_items(root, target, "club")
            if items:
                sources.update(
                    {
                        "akademikKulupPageUsedCache": True,
                        "akademikKulupPdfUsedCache": True,
                        "akademikKulupPdfCacheFile": str(path.relative_to(root)) if path else None,
                    }
                )
                return items, sources
            raise RuntimeError("Akademik Kulüp yerel cache'inde hedef tarih için yemek bulunamadı.")

    items = extract_weekly_items(pdf_text_blocks(club_pdf), f"{target.day}.{target.month}.{target.year}")
    if not items:
        raise RuntimeError("Akademik Kulüp PDF'sinde hedef tarih için yemek bulunamadı.")
    return items, sources


def build_output(
    target: datetime,
    ana_items: list[str],
    club_items: list[str],
    sources: dict[str, object],
    errors: list[str],
    status_override: str | None = None,
    message_override: str | None = None,
    retry_schedule: list[str] | None = None,
) -> dict[str, object]:
    is_weekend = target.isoweekday() >= 6
    if is_weekend:
        return {
            "ok": True,
            "status": "weekend_closed",
            "date": target.strftime("%d.%m.%Y"),
            "isoDate": target.strftime("%Y-%m-%d"),
            "weekday": target.isoweekday(),
            "isWeekend": True,
            "message": "Bugün hafta sonu, yemekhaneler kapalı.",
            "retrySchedule": [],
            "anaYemekhane": [],
            "akademikKulup": [],
            "generatedAt": datetime.now(TIMEZONE).isoformat(timespec="seconds"),
            "sources": {},
            "errors": [],
        }

    status = status_override or ("ok" if ana_items and club_items else "partial")
    if message_override is not None:
        message = message_override
    elif status == "ok":
        message = None
    elif status == "not_published":
        message = (
            "Bugünün yemek listesi ESTÜ sitesinde henüz yayımlanmadı veya yerel cache'te hazır değil. "
            "Yayın günlerinde 08:03–16:03 arasında yeniden kontrol edilecektir."
        )
    else:
        message = (
            "Menülerin bir kısmı henüz yayımlanmadı veya yerel cache'te hazır değil. "
            "İlgili yayın gününde 08:03–16:33 arasında yeniden kontrol edilecektir."
        )

    return {
        "ok": bool(ana_items or club_items),
        "status": status,
        "date": target.strftime("%d.%m.%Y"),
        "isoDate": target.strftime("%Y-%m-%d"),
        "weekday": target.isoweekday(),
        "isWeekend": False,
        "message": message,
        "retrySchedule": retry_schedule if retry_schedule is not None else (REFRESH_TIMES if status != "ok" else []),
        "anaYemekhane": ana_items,
        "akademikKulup": club_items,
        "generatedAt": datetime.now(TIMEZONE).isoformat(timespec="seconds"),
        "sources": sources,
        "errors": errors,
    }


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def collect(
    root: Path,
    target: datetime,
    output_path: Path,
    mode: str = "auto",
) -> int:
    if target.isoweekday() >= 6:
        write_json(output_path, build_output(target, [], [], {}, [], retry_schedule=[]))
        print(f"Hafta sonu: {target.strftime('%d.%m.%Y')}; ESTÜ isteği yapılmayacak.")
        return 0

    state = load_source_state(root)
    refresh_sources = decide_refresh_sources(root, target, state, mode)
    errors: list[str] = []
    sources: dict[str, object] = {
        "collectionMode": mode,
        "refreshSources": sorted(refresh_sources),
        "cacheOnly": not bool(refresh_sources),
    }
    ana_items: list[str] = []
    club_items: list[str] = []

    try:
        ana_items, main_sources = collect_main_source(
            root, target, state, "main" in refresh_sources
        )
        sources.update(main_sources)
    except Exception as exc:
        errors.append(f"Ana yemekhane: {exc}")

    try:
        club_items, club_sources = collect_club_source(
            root, target, state, "club" in refresh_sources
        )
        sources.update(club_sources)
    except Exception as exc:
        errors.append(f"Akademik Kulüp: {exc}")

    if refresh_sources:
        save_source_state(root, state)

    publication_markers = (
        "hedef tarih",
        "bağlantısı bulunamadı",
        "yerel cache'inde",
    )
    all_errors_mean_not_published = bool(errors) and all(
        any(marker in error for marker in publication_markers) for error in errors
    )
    retry_schedule = REFRESH_TIMES if refresh_sources else []

    if not ana_items and not club_items:
        if all_errors_mean_not_published:
            result = build_output(
                target,
                [],
                [],
                sources,
                errors,
                status_override="not_published",
                retry_schedule=retry_schedule,
            )
            write_json(output_path, result)
            print(f"{result['date']} için menü henüz yayımlanmadı; cache/yenileme kontrolü tamamlandı.")
            for error in errors:
                print(f"UYARI: {error}", file=sys.stderr)
            return 0

        print("Menülerin ikisi de üretilemedi; mevcut data/menu.json korunuyor.", file=sys.stderr)
        for error in errors:
            print(f"HATA: {error}", file=sys.stderr)
        return 2

    if errors:
        result = build_output(
            target,
            ana_items,
            club_items,
            sources,
            errors,
            status_override="partial",
            retry_schedule=retry_schedule,
        )
    else:
        result = build_output(
            target,
            ana_items,
            club_items,
            sources,
            errors,
            retry_schedule=retry_schedule,
        )

    write_json(output_path, result)
    print(
        f"{result['date']} menüsü yazıldı: ana={len(ana_items)}, akademik={len(club_items)}, "
        f"durum={result['status']}, yenilenen={','.join(sorted(refresh_sources)) or 'yok'}"
    )
    for error in errors:
        print(f"UYARI: {error}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Test veya manuel çalıştırma için GG.AA.YYYY")
    parser.add_argument(
        "--mode",
        choices=("auto", "cache-only", "main", "club", "refresh"),
        default="auto",
        help="auto: yayın gününde ilgili kaynağı yenile; cache-only: dış istek yapma",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="JSON çıktı yolu; varsayılan data/menu.json",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output_path = Path(args.output).resolve() if args.output else root / "data" / "menu.json"
    try:
        target = parse_date(args.date)
    except ValueError as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 2
    return collect(root, target, output_path, mode=args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
