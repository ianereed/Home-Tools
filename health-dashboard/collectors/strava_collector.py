"""Collect activities and heart rate streams from Strava."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import keyring
from stravalib.client import Client

from .db import get_connection

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "health-dashboard-strava"


def _load_tokens():
    """Load Strava OAuth tokens from keychain."""
    tokens_json = keyring.get_password(KEYRING_SERVICE, "tokens")
    if not tokens_json:
        raise RuntimeError(
            "Strava tokens not found in keychain. Run setup.sh first."
        )
    return json.loads(tokens_json)


def _save_tokens(tokens: dict):
    """Save Strava OAuth tokens to keychain."""
    keyring.set_password(KEYRING_SERVICE, "tokens", json.dumps(tokens))


def _get_strava_client():
    """Create an authenticated Strava client, refreshing tokens if needed."""
    tokens = _load_tokens()

    client_id = keyring.get_password(KEYRING_SERVICE, "client_id")
    client_secret = keyring.get_password(KEYRING_SERVICE, "client_secret")

    if not client_id or not client_secret:
        raise RuntimeError(
            "Strava client_id/client_secret not found in keychain. Run setup.sh first."
        )

    client = Client()

    # Check if token is expired
    expires_at = tokens.get("expires_at", 0)
    if datetime.now(timezone.utc).timestamp() >= expires_at:
        logger.info("Refreshing Strava access token...")
        refresh_response = client.refresh_access_token(
            client_id=int(client_id),
            client_secret=client_secret,
            refresh_token=tokens["refresh_token"],
        )
        if isinstance(refresh_response, dict):
            tokens["access_token"] = refresh_response["access_token"]
            tokens["refresh_token"] = refresh_response["refresh_token"]
            tokens["expires_at"] = refresh_response["expires_at"]
        else:
            tokens["access_token"] = refresh_response.access_token
            tokens["refresh_token"] = refresh_response.refresh_token
            tokens["expires_at"] = refresh_response.expires_at
        _save_tokens(tokens)

    client.access_token = tokens["access_token"]
    return client


def collect_activities(days_back: int = 7):
    """Collect activities and their HR streams from Strava."""
    logger.info(f"Collecting Strava activities for past {days_back} days...")
    client = _get_strava_client()
    conn = get_connection()

    after = datetime.now(timezone.utc) - timedelta(days=days_back)

    try:
        activities = client.get_activities(after=after)

        count = 0
        for activity in activities:
            activity_id = str(activity.id)
            activity_date = activity.start_date_local.strftime("%Y-%m-%d") if activity.start_date_local else ""

            duration_secs = activity.elapsed_time
            if hasattr(duration_secs, 'total_seconds'):
                duration_secs = duration_secs.total_seconds()

            distance_m = activity.distance
            if hasattr(distance_m, 'magnitude'):
                distance_m = float(distance_m.magnitude)
            elif distance_m is not None:
                distance_m = float(distance_m)
            else:
                distance_m = 0

            avg_hr = None
            max_hr = None
            if activity.has_heartrate:
                avg_hr = int(activity.average_heartrate) if activity.average_heartrate else None
                max_hr = int(activity.max_heartrate) if activity.max_heartrate else None

            sport = str(activity.sport_type or activity.type or "unknown")
            # stravalib wraps sport types; extract the raw string
            if "root='" in sport:
                sport = sport.split("root='")[1].rstrip("')")
            # Strava provides kilojoules, convert to kcal (1 kJ ≈ 0.239 kcal)
            calories = None
            if activity.kilojoules:
                calories = int(float(activity.kilojoules) * 0.239)

            conn.execute(
                """INSERT OR REPLACE INTO activities
                   (date, type, duration_minutes, distance_km, avg_hr, max_hr, calories, source, source_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    activity_date,
                    str(sport).lower() if sport else "unknown",
                    round(duration_secs / 60, 1) if duration_secs else 0,
                    round(distance_m / 1000, 2) if distance_m else 0,
                    avg_hr,
                    max_hr,
                    calories,
                    "strava",
                    activity_id,
                ),
            )
            count += 1

        conn.commit()
        logger.info(f"Saved {count} Strava activities")
    except Exception as e:
        logger.error(f"Error collecting Strava activities: {e}")
    finally:
        conn.close()


def collect_hr_streams(days_back: int = 7):
    """Fetch HR time-series for recent activities that have heart rate data."""
    logger.info("Collecting Strava HR streams...")
    client = _get_strava_client()
    conn = get_connection()

    try:
        # Get activity IDs that have HR but no streams yet
        rows = conn.execute(
            """SELECT source_id FROM activities
               WHERE source = 'strava' AND avg_hr IS NOT NULL
               AND date >= date('now', ?)
               AND source_id NOT IN (SELECT DISTINCT activity_id FROM activity_streams)""",
            (f"-{days_back} days",),
        ).fetchall()

        stream_count = 0
        for row in rows:
            activity_id = row[0]
            try:
                streams = client.get_activity_streams(
                    int(activity_id),
                    types=["heartrate", "time"],
                )
                hr_stream = streams.get("heartrate")
                time_stream = streams.get("time")

                if not hr_stream or not time_stream:
                    continue

                hr_data = hr_stream.data
                time_data = time_stream.data

                for t, bpm in zip(time_data, hr_data):
                    conn.execute(
                        """INSERT OR IGNORE INTO activity_streams
                           (activity_id, timestamp_offset, bpm)
                           VALUES (?, ?, ?)""",
                        (activity_id, int(t), int(bpm)),
                    )

                conn.commit()
                stream_count += 1
                logger.info(f"Saved {len(hr_data)} HR points for activity {activity_id}")
            except Exception as e:
                logger.warning(f"Could not fetch stream for activity {activity_id}: {e}")

        logger.info(f"Collected HR streams for {stream_count} activities")
    except Exception as e:
        logger.error(f"Error collecting HR streams: {e}")
    finally:
        conn.close()


def collect_all(days_back: int = 7):
    """Collect all Strava data."""
    collect_activities(days_back)
    collect_hr_streams(days_back)
    logger.info("Strava collection complete.")
