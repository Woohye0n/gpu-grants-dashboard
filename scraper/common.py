"""HTTP fetching, document text extraction and date helpers shared by all sources."""
from __future__ import annotations

import hashlib
import html as _html
import os
import re
import struct
import time
import warnings
import zipfile
import zlib
from datetime import datetime, timedelta, timezone

import requests

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, "cache")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Hosts whose certificate chain is incomplete from this network. Verification is
# attempted first and only downgraded per-host after a real SSLError.
_NO_VERIFY: set[str] = set()


def now_kst() -> datetime:
    return datetime.now(KST)


def _host(url: str) -> str:
    return re.sub(r"^https?://([^/]+).*$", r"\1", url)


def http_get(url: str, *, referer: str | None = None, binary: bool = False,
             timeout: int = 40, retries: int = 3):
    """GET with retries. Returns `requests.Response`."""
    headers = {"User-Agent": UA, "Accept-Language": "ko,en;q=0.8"}
    if referer:
        headers["Referer"] = referer
    if not binary:
        headers["Accept"] = "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"
    host = _host(url)
    last = None
    for attempt in range(retries):
        verify = host not in _NO_VERIFY
        try:
            r = requests.get(url, headers=headers, timeout=timeout, verify=verify)
            r.raise_for_status()
            return r
        except requests.exceptions.SSLError as e:
            last = e
            if verify:
                _NO_VERIFY.add(host)          # retry immediately without verification
                continue
            time.sleep(1.5 * (attempt + 1))
        except Exception as e:                # noqa: BLE001 - network flakiness
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} tries: {url} ({last})")


def get_text(url: str, *, referer: str | None = None, encoding: str | None = None) -> str:
    r = http_get(url, referer=referer)
    if encoding:
        r.encoding = encoding
    elif not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def get_json(url: str, *, referer: str | None = None):
    return http_get(url, referer=referer).json()


def download(url: str, *, referer: str | None = None, suffix: str = "",
             max_mb: int = 25) -> str | None:
    """Download to the cache dir (keyed by URL hash). Returns the local path."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    path = os.path.join(CACHE_DIR, key + suffix)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    try:
        r = http_get(url, referer=referer, binary=True, timeout=90)
    except RuntimeError:
        return None
    if len(r.content) > max_mb * 1024 * 1024 or not r.content:
        return None
    with open(path, "wb") as fh:
        fh.write(r.content)
    return path


# --------------------------------------------------------------------------- #
# HTML -> text
# --------------------------------------------------------------------------- #
def html_to_text(markup: str) -> str:
    """Flatten HTML to readable text, keeping table cells on one line."""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", markup, flags=re.S | re.I)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</t[dh]>", " ", s, flags=re.I)
    s = re.sub(r"</(p|div|tr|li|h[1-6]|table|section)>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s)
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n[ \t]*", "\n", s)
    return clean_stray_q(re.sub(r"\n{2,}", "\n", s).strip())


def clean_stray_q(text: str) -> str:
    """Some CMS editors paste thin/no-break spaces as '?'. Drop them, keep real ones.

    Only lines holding two or more '?' are touched, and only where the '?' is
    glued to the next character — a genuine question mark ends a phrase.
    """
    out = []
    for line in text.split("\n"):
        if line.count("?") >= 2:
            line = re.sub(r"\?(?=\S)", " ", line)
        # '2.?지원내용' — a '?' glued to a Hangul syllable is never punctuation
        line = re.sub(r"(?<=\S)\?(?=[가-힣])", " ", line)
        out.append(re.sub(r"[ \t]{2,}", " ", line))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Attachment -> text  (.hwp / .hwpx / .pdf)
# --------------------------------------------------------------------------- #
_CHAR_CTRL = {0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31}
_SKIP_CTRL = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def hwp5_text(path: str) -> str:
    """Text of a binary HWP 5.x document (OLE container, deflated sections)."""
    import olefile

    if not olefile.isOleFile(path):
        raise ValueError("not an OLE/HWP5 file")
    ole = olefile.OleFileIO(path)
    try:
        head = ole.openstream("FileHeader").read()
        if head[:17] != b"HWP Document File":
            raise ValueError("bad HWP signature")
        compressed = bool(struct.unpack("<I", head[36:40])[0] & 1)
        streams = sorted((e for e in ole.listdir() if e[0] == "BodyText"),
                         key=lambda e: int(re.sub(r"\D", "", e[-1]) or 0))
        out: list[str] = []
        for entry in streams:
            data = ole.openstream(entry).read()
            if compressed:
                data = zlib.decompress(data, -15)
            out.extend(_hwp5_section(data))
    finally:
        ole.close()
    return "\n".join(out)


def _hwp5_section(data: bytes) -> list[str]:
    paras, i, n = [], 0, len(data)
    while i + 4 <= n:
        header = struct.unpack("<I", data[i:i + 4])[0]
        i += 4
        tag, size = header & 0x3FF, (header >> 20) & 0xFFF
        if size == 0xFFF:
            size = struct.unpack("<I", data[i:i + 4])[0]
            i += 4
        payload, i = data[i:i + size], i + size
        if tag == 67:                                    # HWPTAG_PARA_TEXT
            text = _hwp5_para(payload).strip()
            if text:
                paras.append(text)
    return paras


def _hwp5_para(buf: bytes) -> str:
    out, i, n = [], 0, len(buf) - (len(buf) % 2)
    while i < n:
        c = struct.unpack("<H", buf[i:i + 2])[0]
        if c in _CHAR_CTRL:
            if c in (10, 13):
                out.append("\n")
            i += 2
        elif c in _SKIP_CTRL:                            # control with 14-byte body
            out.append(" ")
            i += 16
        else:
            out.append(chr(c))
            i += 2
    return "".join(out)


def hwpx_text(path: str) -> str:
    """Text of an OWPML (.hwpx) document — a zip of XML sections."""
    out: list[str] = []
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if re.search(r"Contents/section\d+\.xml$", n)]
        for name in sorted(names, key=lambda n: int(re.sub(r"\D", "", n.rsplit("/", 1)[-1]) or 0)):
            xml = z.read(name).decode("utf-8", "replace")
            for para in re.split(r"<hp:p\b", xml):
                text = "".join(re.findall(r"<hp:t>(.*?)</hp:t>", para, re.S))
                text = _html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
                if text:
                    out.append(text)
    return "\n".join(out)


def pdf_text(path: str) -> str:
    try:
        try:
            import pymupdf                               # PyMuPDF: better table layout
        except ImportError:
            import fitz as pymupdf                       # older releases
        with pymupdf.open(path) as doc:
            return "\n".join(page.get_text("text") for page in doc)
    except Exception:                                    # noqa: BLE001
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)


def doc_text(path: str, filename: str = "") -> str:
    """Extract text from an attachment, dispatching on its real content."""
    name = (filename or path).lower()
    try:
        if name.endswith(".pdf"):
            return pdf_text(path)
        if name.endswith(".hwpx"):
            return hwpx_text(path)
        if name.endswith(".hwp"):
            return hwp5_text(path)
        with open(path, "rb") as fh:
            magic = fh.read(8)
        if magic[:4] == b"%PDF":
            return pdf_text(path)
        if magic[:4] == b"PK\x03\x04":
            return hwpx_text(path)
        if magic[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            return hwp5_text(path)
    except Exception:                                    # noqa: BLE001 - best effort
        return ""
    return ""


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
def iso_from_api(value: str | None) -> str | None:
    """'2026-04-01T10:05:00.000+00:00' -> '2026-04-01T19:05' (KST)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y-%m-%dT%H:%M")


def parse_dt(text: str | None) -> str | None:
    """'2026-09-16 15:00' / '2026.9.16' / '26. 9. 16.' -> ISO string."""
    if not text:
        return None
    s = text.strip()
    m = re.search(r"(\d{4})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})", s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = re.search(r"[’'‘]?(\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})", s)
        if not m:
            return None
        y, mo, d = 2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    tail = s[m.end():m.end() + 16]
    hm = re.search(r"(\d{1,2})\s*[:시]\s*(\d{2})", tail)
    try:
        if hm:
            return datetime(y, mo, d, int(hm.group(1)), int(hm.group(2))).strftime("%Y-%m-%dT%H:%M")
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_range(text: str) -> tuple[str | None, str | None]:
    """Split 'A ~ B' and parse both sides."""
    parts = re.split(r"\s*[~∼〜–—]\s*|\s+부터\s+", text, maxsplit=1)
    if len(parts) == 2:
        return parse_dt(parts[0]), parse_dt(parts[1]) or _same_year_end(parts[0], parts[1])
    return parse_dt(text), None


def _same_year_end(left: str, right: str) -> str | None:
    """'2026. 10. 12. ~ 1. 11.' — borrow the year from the left side."""
    m = re.search(r"(\d{4})", left)
    if not m:
        return None
    m2 = re.search(r"(\d{1,2})[.\-/월]\s*(\d{1,2})", right)
    if not m2:
        return None
    return parse_dt(f"{m.group(1)}.{m2.group(1)}.{m2.group(2)}")


def dday(deadline: str | None, today: datetime | None = None) -> int | None:
    if not deadline:
        return None
    try:
        end = datetime.strptime(deadline[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return (end - (today or now_kst()).date()).days
