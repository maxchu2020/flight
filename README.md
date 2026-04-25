# Special Livery Flight Tracker

Monitors Cathay Pacific special livery aircraft for appearances at target airports. Sends Telegram alerts when a tracked aircraft is inbound or on the ground.

## Tracked aircraft

| Registration | Airline | Livery |
|---|---|---|
| B-KQU | Cathay Pacific | Spirit of Hong Kong |
| B-LJE | Cathay Pacific | Better Aviation |
| B-LRJ | Cathay Pacific | Better Aviation |
| D-ABYN | Lufthansa | Special livery |
| D-AIMH | Lufthansa | Special livery |
| D-ABPU | Lufthansa | Special livery |
| D-AIXL | Lufthansa | Special livery |
| A6-EET | Emirates | Special livery |
| A6-EOD | Emirates | Special livery |
| A6-EUH | Emirates | Special livery |
| A6-EEW | Emirates | Special livery |
| C-GXLR | Air Canada | First A321XLR |

## Target airports

JFK · YYZ · YVR · ANC

## Alert conditions

- **Inbound** — aircraft is airborne with destination at a target airport (includes ETA)
- **On ground** — aircraft landed at a target airport within the last 12 hours

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in credentials
```

`.env` fields:

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

## Usage

```bash
python3 tracker.py
```

Cron runs four times daily at 00:00, 06:00, 12:00, and 18:00 UTC (every 6 hours):

```
0 0,6,12,18 * * * /usr/bin/python3 /opt/project/flight/tracker.py >> /opt/project/flight/cron.log 2>&1
```

## Data source

FlightRadar24 unofficial API (`api.flightradar24.com/common/v1/flight/list.json`). No API key required.
