#!/usr/bin/env python3
"""
Special Livery Flight Tracker
Monitors special livery aircraft for appearances at target airports
via FlightRadar24 flight/list API. Runs four times daily via cron
(00/06/12/18 Beijing time).
"""

import os
import json
import time
import logging
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from dotenv import load_dotenv

# Display timezone for alerts; follows EDT/EST automatically
LOCAL_TZ = ZoneInfo('America/Toronto')
TIME_FMT = '%m/%d %H:%M %Z'

load_dotenv(dotenv_path=Path('/opt/project/flight/.env'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/opt/project/flight/tracker.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

AIRCRAFT        = ['B-KQU', 'B-LJE', 'B-LRJ', 'D-ABYN', 'D-AIMH', 'D-ABPU', 'D-AIXL',
                   'A6-EET', 'A6-EOD', 'A6-EUH', 'A6-EEW', 'C-GXLR']
TARGET_AIRPORTS = {'JFK', 'YYZ', 'ANC', 'YVR'}
ON_GROUND_WINDOW_S = 12 * 3600  # alert if landed at target within last 12 h

# Remembers which (flight, alert type) pairs were already sent, so each
# inbound / on-ground event alerts once instead of on every cron run
STATE_FILE      = Path('/opt/project/flight/alerted.json')
STATE_RETAIN_S  = 3 * 24 * 3600
TELEGRAM_MAX_RETRIES = 3

TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT  = os.getenv('TELEGRAM_CHAT_ID')

FR24_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/122.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
    'Origin': 'https://www.flightradar24.com',
    'Referer': 'https://www.flightradar24.com/',
}


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f'State file unreadable, starting fresh: {e}')
        return {}


def save_state(state: dict) -> None:
    cutoff = int(time.time()) - STATE_RETAIN_S
    state = {k: v for k, v in state.items() if v >= cutoff}
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(STATE_FILE)


def send_telegram(text: str, retries: int = TELEGRAM_MAX_RETRIES) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        logger.error('Telegram credentials not configured')
        return False
    try:
        if len(text) > 4000:
            text = text[:3997] + '...'
        resp = requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT, 'text': text, 'parse_mode': 'HTML'},
            timeout=10
        )
        if resp.status_code == 200:
            logger.info('Telegram: sent')
            return True
        if resp.status_code == 429 and retries > 0:
            wait = resp.json().get('parameters', {}).get('retry_after', 30)
            logger.warning(f'Telegram rate-limited, retrying in {wait}s')
            time.sleep(wait)
            return send_telegram(text, retries - 1)
        logger.error(f'Telegram error {resp.status_code}: {resp.text}')
    except Exception as e:
        logger.error(f'Telegram exception: {e}')
    return False


def check_aircraft(reg: str) -> list:
    """
    Query FR24 flight list for the registration.
    Returns list of finding dicts for any target-airport match.

    Two cases:
      inbound  — live=True,  destination in TARGET_AIRPORTS (currently flying there)
      on_ground — live=False, destination in TARGET_AIRPORTS, landed within 12 h
    """
    findings = []
    try:
        resp = requests.get(
            'https://api.flightradar24.com/common/v1/flight/list.json',
            params={'fetchBy': 'reg', 'query': reg, 'limit': 10, 'page': 1},
            headers=FR24_HEADERS,
            timeout=20
        )
        if resp.status_code != 200:
            logger.error(f'{reg}: FR24 list HTTP {resp.status_code}')
            return findings

        flights = resp.json().get('result', {}).get('response', {}).get('data', []) or []
        if not flights:
            logger.info(f'{reg}: no flight history returned')
            return findings

        now_ts = int(datetime.now(timezone.utc).timestamp())

        for f in flights:
            live     = f.get('status', {}).get('live', False)
            ident    = f.get('identification') or {}
            callsign = ident.get('callsign') or \
                       (ident.get('number') or {}).get('default', reg)

            ap   = f.get('airport') or {}
            orig = (ap.get('origin') or {}).get('code') or {}
            dst  = (ap.get('destination') or {}).get('code') or {}
            origin_iata = orig.get('iata', '')
            dest_iata   = dst.get('iata', '')

            if dest_iata not in TARGET_AIRPORTS:
                continue

            t          = f.get('time') or {}
            eta_ts     = (t.get('estimated') or {}).get('arrival') or \
                         (t.get('other') or {}).get('eta') or 0
            arrival_ts = (t.get('real') or {}).get('arrival') or eta_ts or 0
            status_txt = (f.get('status') or {}).get('text', '')
            # generic.status looks like {"text": "landed", "type": "arrival"}
            generic_status = ((f.get('status') or {}).get('generic') or {}) \
                             .get('status', {}).get('text', '')
            # FR24 flight id is stable per leg; fall back if it is missing
            flight_key = ident.get('id') or f'{callsign}-{origin_iata}-{dest_iata}-{eta_ts}'

            eta_str = ''
            if eta_ts:
                eta_str = datetime.fromtimestamp(eta_ts, tz=LOCAL_TZ).strftime(TIME_FMT)

            if live:
                # Currently airborne, destination is a target airport
                findings.append({
                    'key': f'{reg}:{flight_key}:inbound',
                    'type': 'inbound',
                    'airport': dest_iata,
                    'callsign': callsign,
                    'origin': origin_iata,
                    'status': status_txt,
                    'eta': eta_str,
                })

            else:
                # Not live — check if it landed at target within the window
                if generic_status == 'landed' and arrival_ts and (now_ts - arrival_ts) < ON_GROUND_WINDOW_S:
                    landed_str = datetime.fromtimestamp(arrival_ts, tz=LOCAL_TZ).strftime(TIME_FMT)
                    findings.append({
                        'key': f'{reg}:{flight_key}:on_ground',
                        'type': 'on_ground',
                        'airport': dest_iata,
                        'callsign': callsign,
                        'origin': origin_iata,
                        'status': status_txt,
                        'landed': landed_str,
                    })

    except Exception as e:
        logger.error(f'{reg}: unexpected error: {e}')

    return findings


def build_message(reg: str, findings: list) -> str:
    now = datetime.now(LOCAL_TZ).strftime('%Y-%m-%d %H:%M %Z')
    lines = [
        '✈️ <b>Special Livery</b>',
        f'<b>{reg}</b>  ·  <i>{now}</i>',
        '',
    ]
    for f in findings:
        if f['type'] == 'inbound':
            lines.append(f"🛬 <b>Inbound · {f['airport']}</b>")
            lines.append(f"   {f['callsign']}  {f['origin']} → {f['airport']}")
            if f.get('status'):
                lines.append(f"   {f['status']}")
            if f.get('eta'):
                lines.append(f"   ETA {f['eta']}")
        elif f['type'] == 'on_ground':
            lines.append(f"🅿️ <b>On ground · {f['airport']}</b>")
            lines.append(f"   {f['callsign']}  {f['origin']} → {f['airport']}")
            if f.get('landed'):
                lines.append(f"   Landed {f['landed']}")
            if f.get('status'):
                lines.append(f"   {f['status']}")
        lines.append('')
    return '\n'.join(lines).strip()


def main():
    logger.info(
        f'=== Special Livery Tracker  aircraft={",".join(AIRCRAFT)}'
        f'  targets={",".join(sorted(TARGET_AIRPORTS))} ==='
    )
    state = load_state()
    for reg in AIRCRAFT:
        logger.info(f'Checking {reg}...')
        findings = check_aircraft(reg)
        new = [f for f in findings if f['key'] not in state]
        if new:
            logger.info(f'{reg}: {len(new)} new finding(s) — alerting')
            if send_telegram(build_message(reg, new)):
                now_ts = int(time.time())
                for f in new:
                    state[f['key']] = now_ts
                save_state(state)
        elif findings:
            logger.info(f'{reg}: {len(findings)} finding(s) already alerted, skipping')
        else:
            logger.info(f'{reg}: no matches at target airports')
        time.sleep(3)
    save_state(state)
    logger.info('=== Done ===')


if __name__ == '__main__':
    main()
