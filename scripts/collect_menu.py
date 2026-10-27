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
import subprocess
import sys
import tempfile
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

def extract_page_links(root: Path) -> tuple[str, str]:
    """ESTÜ ana sayfasındaki dinamik menü linklerini bulur, yoksa eski sabit linkleri kullanır."""
    base_url = "https://saglikkulturspor.eskisehir.edu.tr/tr"
    main_fallback = f"{ESTU_BASE}/tr/Icerik/Detay/yemekhaneler"
    club_fallback = f"{ESTU_BASE}/tr/Icerik/Detay/gunluk-menu"

    try:
        html_bytes, _, _ = get_cached_or_fetch(root, base_url, "pages", "_index.html")
        html_str = html_bytes.decode('utf-8', errors='replace')
        parser = AnchorParser()
        parser.feed(html_str)

        main_url = None
        club_url = None

        for href, label in parser.links:
            text = label.casefold()
            resolved = resolve_estu_url(href, base_url)
            if not resolved:
                continue
            if club_url is None and ("haftalık" in text or "akademik" in text or "haftalik" in text):
                club_url = resolved
            if main_url is None and ("yemekhane" in text or "menü" in text or "menu" in text):
                main_url = resolved

        return main_url or main_fallback, club_url or club_fallback
    except Exception:
        return main_fallback, club_fallback


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

DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")

def pdftotext_layout(pdf_data: bytes) -> list[str]:
    """PDF'i Poppler'ın grid koruyan metin çıktısına çevirir.

    Koordinat parser'ının aksine bu katman PDF üreticisinin nesne sırasına değil,
    kullanıcıya görünen satır/kolon düzenine dayanır. pdftotext mevcut değilse
    hata kontrollü biçimde üst katmana aktarılır.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf_file:
        pdf_file.write(pdf_data)
        pdf_file.flush()
        try:
            result = subprocess.run(
                ["pdftotext", "-layout", "-enc", "UTF-8", pdf_file.name, "-"],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("pdftotext bulunamadı; Poppler kurulmalıdır.") from exc
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"pdftotext başarısız: {detail or 'bilinmeyen hata'}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("pdftotext zaman aşımına uğradı.") from exc
    return result.stdout.decode("utf-8", errors="replace").splitlines()

def clean_grid_cell(value: str, source: str) -> str:
    """Bir grid hücresinden miktar, kalori, fiyat ve tasarım metnini ayırır."""
    value = normalize_spaces(value)
    if not value:
        return ""
    if source == "weekly":
        value = re.sub(r"\s+\d+(?:[.,]\d+)?\s*kcal\b", "", value, flags=re.I)
        value = re.sub(r"\s+\d+(?:[.,]\d+)?\s*(?:₺|TL)", "", value, flags=re.I)
        value = re.sub(r"\s+\d+(?:[.,]\d+)?\s*", " ", value)
    else:
        value = re.sub(r"\s+\d+(?:[.,]\d+)?\s*(?:gr\.?|ml\.?)", "", value, flags=re.I)
        value = re.sub(r"\s+\d+(?:[.,]\d+)?\s*$", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value or re.fullmatch(r"[+\-]?Garn\.?", value, re.I):
        return value
    if re.search(
        r"SERVİS SAATİ|MİKTAR|KALORİ|TARİHLİ HAFTALIK MENÜ|GENEL YEMEK MENÜSÜ|"
        r"PAZARTESİ|SALI|ÇARŞAMBA|PERŞEMBE|CUMA|CUMARTESİ|PAZAR",
        value,
        re.I,
    ):
        return ""
    return value

def extract_grid_items(pdf_data: bytes, source: str) -> dict[str, list[str]]:
    """Tarih başlıklarından kolonları öğrenerek tüm günlerin menülerini çıkarır."""
    lines = pdftotext_layout(pdf_data)
    result: dict[str, list[str]] = {}

    header_indices: list[int] = []
    for index, line in enumerate(lines):
        matches = list(DATE_RE.finditer(line))
        if matches:
            if source == "weekly" and len(matches) < 5:
                continue
            header_indices.append(index)

    for i, header_index in enumerate(header_indices):
        line = lines[header_index]
        matches = list(DATE_RE.finditer(line))
        dates = [(m.group(1), m.group(2), m.group(3)) for m in matches]
        all_starts = [m.start() for m in matches]

        end_index = len(lines)
        if i + 1 < len(header_indices):
            end_index = header_indices[i + 1]

        for index in range(header_index + 1, end_index):
            if source == "weekly" and re.search(r"SERVİS SAATİMİZ", lines[index], re.I):
                end_index = index
                break

        cols = len(dates)
        chunks_per_col: list[list[tuple[str, bool]]] = [[] for _ in range(cols)]

        for index in range(header_index + 1, end_index):
            cur_line = lines[index]
            if source == "weekly":
                records = list(re.finditer(
                    r"(.+?)\s+\d+(?:[.,]\d+)?\s*kcal\s+\d+(?:[.,]\d+)?\s*(?:₺|TL)",
                    cur_line, re.IGNORECASE))
            else:
                records = list(re.finditer(
                    r"(.+?)\s+\d+(?:[.,]\d+)?\s*(?:gr\.?|ml\.?)\s+\d+(?:[.,]\d+)?",
                    cur_line, re.IGNORECASE))

            for c in range(cols):
                left_boundary = (all_starts[c - 1] + all_starts[c]) / 2 if c > 0 else -1
                right_boundary = (all_starts[c] + all_starts[c + 1]) / 2 if c + 1 < cols else float("inf")

                target_records = [r for r in records if left_boundary <= r.start() < right_boundary]
                if target_records:
                    cell = clean_grid_cell(target_records[0].group(1), source)
                    if cell and not DATE_RE.search(cell):
                        chunks_per_col[c].append((cell, True))
                    continue

                start = max(0, int(left_boundary))
                stop = min(len(cur_line), int(right_boundary)) if right_boundary != float("inf") else len(cur_line)
                continuation = clean_grid_cell(cur_line[start:stop], source)
                if continuation and len(continuation) <= 24 and not DATE_RE.search(continuation) and not re.search(r"\d|₺|\bTL\b|\bkcal\b", continuation, re.I):
                    chunks_per_col[c].append((continuation, False))

        for c in range(cols):
            items: list[str] = []
            for chunk, is_record in chunks_per_col[c]:
                if items and not is_record and (chunk.casefold() in {"çorbasi", "çorbası", "pilavı", "pilavi"} or chunk.casefold().endswith(("çorbasi", "çorbası"))):
                    items[-1] = normalize_spaces(f"{items[-1]} {chunk}")
                else:
                    items.append(chunk)

            d_str = f"{dates[c][0].zfill(2)}.{dates[c][1].zfill(2)}.{dates[c][2]}"
            result[d_str] = remove_duplicate_items(items)

    return result

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

def load_master_cache(root: Path, kind: str) -> dict[str, list[str]]:
    path = root / "data" / f"master_{kind}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_master_cache(root: Path, kind: str, data: dict[str, list[str]]) -> None:
    path = root / "data" / f"master_{kind}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, dir=path.parent) as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        tf.write("\n")
        temp_name = tf.name
    import os
    os.replace(temp_name, path)


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
                        "pageUrl": page_url,
                        "pdfUrl": main_url,
                        "pdfLabel": sources.get("anaYemekhanePdfLinkLabel", ""),
                    }
                if club_url:
                    state["akademikKulup"] = {
                        "pageUrl": page_url,
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
    for path in cached_pdf_candidates(root):
        try:
            all_items = extract_grid_items(
                path.read_bytes(), "monthly" if source == "main" else "weekly"
            )
            items = all_items.get(target.strftime('%d.%m.%Y'), [])
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
    page_url: str,
) -> tuple[list[str], dict[str, object]]:
    master = load_master_cache(root, "main")
    target_date_str = target.strftime('%d.%m.%Y')
    if target_date_str in master and not refresh:
        return master[target_date_str], {"anaYemekhanePage": page_url, "usedMasterCache": True}

    sources: dict[str, object] = {"anaYemekhanePage": page_url}
    entry = state.setdefault("anaYemekhane", {})

    if refresh:
        main_html, page_stale, main_final_url = get_cached_or_fetch(
            root, page_url, "pages", ".html"
        )
        main_link = select_monthly_pdf(extract_pdf_links(main_html, page_url), target)
        if main_link is None:
            raise RuntimeError("Ana yemekhane için aylık PDF bağlantısı bulunamadı.")
        main_pdf, pdf_stale, pdf_final_url = get_pdf_cached_or_fetch(root, main_link.url)
        entry.update(
            {
                "pageUrl": page_url,
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

    all_items = extract_grid_items(main_pdf, "monthly")
    if all_items:
        master.update(all_items)
        save_master_cache(root, "main", master)
    items = all_items.get(target.strftime('%d.%m.%Y'), [])
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
    page_url: str,
) -> tuple[list[str], dict[str, object]]:
    master = load_master_cache(root, "club")
    target_date_str = target.strftime('%d.%m.%Y')
    if target_date_str in master and not refresh:
        return master[target_date_str], {"akademikKulupPage": page_url, "usedMasterCache": True}

    sources: dict[str, object] = {"akademikKulupPage": page_url}
    entry = state.setdefault("akademikKulup", {})

    if refresh:
        club_html, page_stale, club_final_url = get_cached_or_fetch(
            root, page_url, "pages", ".html"
        )
        club_link = select_weekly_pdf(extract_pdf_links(club_html, page_url))
        if club_link is None:
            raise RuntimeError("Akademik Kulüp için haftalık PDF bağlantısı bulunamadı.")
        club_pdf, pdf_stale, pdf_final_url = get_pdf_cached_or_fetch(root, club_link.url)
        entry.update(
            {
                "pageUrl": page_url,
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

    all_items = extract_grid_items(club_pdf, "weekly")
    if all_items:
        master.update(all_items)
        save_master_cache(root, "club", master)
    items = all_items.get(target.strftime('%d.%m.%Y'), [])
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
    import tempfile, os, json
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False, dir=path.parent) as tf:
        json.dump(value, tf, ensure_ascii=False, indent=2)
        tf.write("\n")
        temp_name = tf.name
    os.replace(temp_name, path)

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
    main_page_url, club_page_url = extract_page_links(root)
    sources: dict[str, object] = {
        "collectionMode": mode,
        "refreshSources": sorted(refresh_sources),
        "cacheOnly": not bool(refresh_sources),
    }
    ana_items: list[str] = []
    club_items: list[str] = []

    try:
        ana_items, main_sources = collect_main_source(
            root, target, state, "main" in refresh_sources, main_page_url
        )
        sources.update(main_sources)
    except Exception as exc:
        errors.append(f"Ana yemekhane: {exc}")

    try:
        club_items, club_sources = collect_club_source(
            root, target, state, "club" in refresh_sources, club_page_url
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
