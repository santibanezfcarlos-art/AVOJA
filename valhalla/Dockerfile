FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
# zoneinfo uses this pinned package if the image has no system timezone database.
RUN python -m pip install --no-cache-dir tzdata==2025.2 cryptography==46.0.2 openpyxl==3.1.5 \
    && useradd --create-home --uid 10001 valhalla \
    && mkdir -p /app/data \
    && chown valhalla:valhalla /app/data
COPY skedu_contacts.py app.py mailer.py payments.py notifications.py google_auth.py microsoft_auth.py rules.py ttlock_daily.py ttlock_worker.py ttlock_manual_test.py ttlock_recipients.py ttlock_distribution.py ttlock_distribution_preview.py ttlock_email_html.py ./
COPY whatsapp_campaigns.py ./
COPY static ./static
USER valhalla
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/',timeout=4).read()"
CMD ["sh", "-c", "exec python app.py --host 0.0.0.0 --port \"${PORT:-8080}\" --secure-cookie"]
