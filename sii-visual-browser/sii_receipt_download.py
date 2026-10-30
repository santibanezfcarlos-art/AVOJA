"""Download an already-issued, verified SII PDF; never submit a tax form."""
import base64
import binascii
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse


def _pdf_bytes(encoded):
    """Only accept valid original PDF bytes, not HTML or a second document."""
    if not isinstance(encoded, str) or not encoded:
        return None
    value = encoded.strip()
    if value.startswith("data:"):
        if not value.lower().startswith("data:application/pdf;base64,"):
            return None
        value = value.split(",", 1)[1]
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return None
    if (not 100 <= len(data) <= 5_000_000 or not data.startswith(b"%PDF-")
            or b"%%EOF" not in data[-2048:]):
        return None
    return data


def _verified_link(page, current):
    url = current.get("pdf_url")
    if not isinstance(url, str) or not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme == "https":
        # Preserve the already-working HTTPS download path. The URL still
        # must match the exact SII Vue receipt and its visible Descargar link.
        if not parsed.hostname or parsed.username or parsed.password:
            return None
    elif parsed.scheme == "blob":
        if not url.startswith("blob:https://eboleta.sii.cl/"):
            return None
    else:
        return None
    link = page.get_by_role("link", name=re.compile(r"\bDescargar\b", re.I))
    if (link.count() != 1 or not link.is_visible()
            or not link.is_enabled() or link.get_attribute("href") != url):
        return None
    return link


def download_official_pdf(page, snapshot, *, wait_seconds=30):
    """Prefer the verified SII component's PDF; otherwise wait and download once.

    This code performs no tax submission. It requires the same verified folio,
    DTE 41 and amount that were persisted before any download attempt.
    """
    from issue_canary import official_snapshot
    deadline = time.monotonic() + wait_seconds
    while True:
        current = official_snapshot(page, snapshot["amount"])
        if not current or any(current.get(k) != snapshot.get(k)
                              for k in ("folio", "dte", "amount")):
            raise ValueError("download_document_changed")
        inline = _pdf_bytes(current.get("pdf"))
        if inline:
            return base64.b64encode(inline).decode("ascii")
        link = _verified_link(page, current)
        if link is not None:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("official_pdf_unavailable_after_wait")
        page.wait_for_timeout(min(1000, max(1, int(remaining * 1000))))

    url = current["pdf_url"]
    routed = urlparse(url).scheme == "https"
    pattern = re.compile("^" + re.escape(url) + "$")

    def attachment(route):
        if route.request.method != "GET":
            route.abort()
            return
        response = route.fetch(timeout=30000)
        headers = dict(response.headers)
        headers["content-disposition"] = 'attachment; filename="boleta41_folio' + snapshot["folio"] + '.pdf"'
        route.fulfill(response=response, headers=headers)

    # A blob URL is already served by the browser and needs no interception.
    if routed:
        page.route(pattern, attachment)
    try:
        with page.expect_download(timeout=35000) as pending:
            link.click(timeout=10000)
        download = pending.value
        with tempfile.TemporaryDirectory(prefix="psc-sii-pdf-") as folder:
            target = Path(folder) / "official.pdf"
            download.save_as(target)
            if not 100 <= target.stat().st_size <= 5_000_000:
                raise ValueError("official_download_size_invalid")
            data = target.read_bytes()
        if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-2048:]:
            raise ValueError("official_download_invalid_pdf")
        inline = _pdf_bytes(current.get("pdf"))
        if inline and data != inline:
            raise ValueError("official_download_pdf_mismatch")
        return base64.b64encode(data).decode("ascii")
    finally:
        if routed:
            page.unroute(pattern, attachment)
