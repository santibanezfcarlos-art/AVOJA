"""Render the published agenda's calendar at existing rebooking URLs."""
import json
import re
from datetime import datetime
from pathlib import Path
from flask import Response


def render_rebooking_calendar(booking):
    root = Path(__file__).resolve().parent
    public = (root.parent / 'psicologia' / 'index.html').read_text(encoding='utf-8')
    styles = '\n'.join(re.findall(r'<style\b[^>]*>[\s\S]*?</style>', public))
    header = re.search(r'<header\b[\s\S]*?</header>', public).group(0)
    header = re.sub(r'href="(?!https?://)(?:\./)?([^"\s]*)"',
                    r'href="https://pscsantibanez.cl/\1"', header)
    # These are the actual public calendar elements and styles, not a second design.
    calendar = public.split('<div class="layout" id="patientView">', 1)[1].split('<aside class="card">', 1)[0]
    calendar = calendar.replace('Elige un horario disponible y completa tu reserva.',
                                'Elige un horario disponible y confirma la reprogramación.')
    start = datetime.fromisoformat(booking['starts_at'])
    end = datetime.fromisoformat(booking['ends_at'])
    context = json.dumps({'service': booking['service'], 'start': start.isoformat(),
                          'duration': int((end-start).total_seconds()/60)},
                         ensure_ascii=True).replace('<', '\\u003c')
    template = (root / 'rebooking_calendar.html').read_text(encoding='utf-8')
    for key, value in {'__SHARED_STYLES__': styles, '__SHARED_HEADER__': header,
                       '__SHARED_CALENDAR__': calendar, '__BOOKING_CONTEXT__': context}.items():
        template = template.replace(key, value)
    response = Response(template, content_type='text/html; charset=utf-8')
    response.headers.update({'Cache-Control': 'no-store, private',
                             'Referrer-Policy': 'no-referrer',
                             'X-Frame-Options': 'DENY'})
    return response
