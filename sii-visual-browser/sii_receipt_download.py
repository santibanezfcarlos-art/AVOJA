"""Download the already confirmed SII document; never submit a tax form."""
import base64
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse


def download_official_pdf(page, snapshot):
    from issue_canary import official_snapshot
    current = official_snapshot(page, snapshot["amount"])
    if not current or any(current.get(k) != snapshot.get(k)
                          for k in ("folio", "dte", "amount")):
        raise ValueError("download_document_changed")
    url = current.get("pdf_url")
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("official_download_url_missing")
    # SII renders Descargar as an anchor to urlPdf, not a button. Match both
    # its exact official URL and its visible name, excluding Compartir.
    link = page.get_by_role("link", name=re.compile(r"\bDescargar\b", re.I))
    if (link.count() != 1 or not link.is_visible()
            or not link.is_enabled() or link.get_attribute("href") != url):
        raise ValueError("official_download_link_unverified")
    route_pattern = re.compile("^" + re.escape(url) + "$")

    def attachment(route):
        # Keep the receipt page open: deliver the ORIGINAL GET response as an
        # attachment instead of opening Chromium's PDF viewer in this tab.
        if route.request.method != "GET":
            route.abort()
            return
        response = route.fetch(timeout=30000)
        headers = dict(response.headers)
        headers["content-disposition"] = 'attachment; filename="boleta41_folio' + snapshot["folio"] + '.pdf"'
        route.fulfill(response=response, headers=headers)

    page.route(route_pattern, attachment)
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
        # If SII supplied both representations they must agree. Never attach
        # an unrelated download to the purchase or silently use a substitute.
        encoded = current.get("pdf")
        if encoded:
            expected = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
            if data != expected:
                raise ValueError("official_download_pdf_mismatch")
        return base64.b64encode(data).decode("ascii")
    finally:
        page.unroute(route_pattern, attachment)
