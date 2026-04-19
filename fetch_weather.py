#!/usr/bin/env python3
"""
Weather Underground / The Weather Company PWS current observation fetcher
Runs every 15 minutes via cron → saves raw JSONB to PostgreSQL
"""

import os
from datetime import datetime, timezone

import requests
import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# Config from .env
# ──────────────────────────────────────────────
API_KEY = os.getenv("WEATHER_API_KEY")
STATION_ID = os.getenv("PWS_STATION_ID")
DB_URL = os.getenv("DATABASE_URL")  # e.g. postgresql://user:pass@127.0.0.1:5432/saas

if not all([API_KEY, STATION_ID, DB_URL]):
    print("Missing required env vars: WEATHER_API_KEY, PWS_STATION_ID, DATABASE_URL")
    raise SystemExit(1)


# ──────────────────────────────────────────────
def fetch_and_store():
    url = (
        "https://api.weather.com/v2/pws/observations/current"
        f"?apiKey={API_KEY}"
        f"&stationId={STATION_ID}"
        "&format=json"
        "&units=e"  # e = English (F, mph, inHg, etc.) — change to 'm' for metric
        "&numericPrecision=decimal"
    )

    try:
        resp = requests.get(url, timeout=12)
        resp.raise_for_status()
        payload = resp.json()

        observations = payload.get("observations", [])
        if not observations:
            print("No observations returned")
            return

        obs = observations[0]  # usually only one — the most recent

        # ── FIX 1: use obsTimeUtc string (already UTC) instead of epoch ──
        # epoch-based datetime.fromtimestamp() uses local time — wrong for TIMESTAMPTZ
        obs_time_str = obs.get("obsTimeUtc", "")
        try:
            obs_time = datetime.strptime(obs_time_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except (ValueError, AttributeError):
            # Fall back to epoch if string is missing/malformed
            obs_epoch = obs.get("epoch")
            obs_time = (
                datetime.fromtimestamp(obs_epoch, tz=timezone.utc)
                if obs_epoch
                else datetime.now(timezone.utc)
            )

        # ── FIX 2: imperial fields are nested under obs["imperial"], not obs ──
        # When units=e, temp/wind/pressure/precip live in obs["imperial"]
        # Only humidity, winddir, uv, solarRadiation stay at the top level
        imp = obs.get("imperial", {})

        # Connect & insert
        conn = psycopg2.connect(DB_URL)
        conn.autocommit = False
        cur = conn.cursor()

        # Ensure table exists (run once or via migration)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pws_observations (
                id              BIGSERIAL PRIMARY KEY,
                station_id      TEXT NOT NULL,
                observed_at     TIMESTAMP WITH TIME ZONE NOT NULL,
                fetched_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                raw_json        JSONB NOT NULL,
                temp_f          REAL,
                humidity        REAL,
                wind_speed_mph  REAL,
                wind_gust_mph   REAL,
                pressure_in     REAL,
                precip_rate_in  REAL
            );

            CREATE INDEX IF NOT EXISTS idx_pws_obs_station_time
                ON pws_observations (station_id, observed_at DESC);
        """
        )

        cur.execute(
            """
            INSERT INTO pws_observations (
                station_id, observed_at, raw_json,
                temp_f, humidity, wind_speed_mph, wind_gust_mph,
                pressure_in, precip_rate_in
            ) VALUES (
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s
            )
            ON CONFLICT DO NOTHING;   -- skip duplicates if epoch matches
        """,
            (
                STATION_ID,
                obs_time,
                Json(obs),  # full raw observation as JSONB
                imp.get("temp"),  # FIX: was obs.get("temp")      — wrong level
                obs.get("humidity"),  # correct: humidity is top-level
                imp.get("windSpeed"),  # FIX: was obs.get("windspeed") — wrong key + level
                imp.get("windGust"),  # FIX: was obs.get("windgust")  — wrong key + level
                imp.get("pressure"),  # FIX: was obs.get("pressure")  — wrong level
                imp.get("precipRate"),  # FIX: was obs.get("precipRate")— wrong level
            ),
        )

        conn.commit()
        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Saved observation for {STATION_ID} at {obs_time}"
        )

    except requests.RequestException as e:
        print(f"API request failed: {e}")
    except psycopg2.Error as e:
        print(f"Database error: {e}")
        if "conn" in locals():
            conn.rollback()
    except Exception as e:
        print(f"Unexpected error: {e}")
    finally:
        if "cur" in locals():
            cur.close()
        if "conn" in locals():
            conn.close()


# ──────────────────────────────────────────────
if __name__ == "__main__":
    fetch_and_store()
