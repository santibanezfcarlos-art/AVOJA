FROM mcr.microsoft.com/playwright/python:v1.55.0-noble
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb x11vnc novnc websockify nginx supervisor gettext-base \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py /srv/app.py
COPY index.html /srv/index.html
COPY nginx.conf.template /srv/nginx.conf.template
COPY supervisord.conf /etc/supervisor/conf.d/sii-visual.conf
COPY entrypoint.sh /srv/entrypoint.sh
RUN chmod 0755 /srv/entrypoint.sh
EXPOSE 10000
CMD ["/srv/entrypoint.sh"]
