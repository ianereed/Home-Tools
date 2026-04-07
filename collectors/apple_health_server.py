"""Lightweight HTTP server to receive Apple Health data from Health Auto Export app."""

import json
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

from .db import get_connection, init_db

logger = logging.getLogger(__name__)

PORT = 8095


class HealthDataHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
            self._process_health_data(data)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "ok"}')
        except Exception as e:
            logger.error(f"Error processing health data: {e}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def do_GET(self):
        """Health check endpoint."""
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Health Auto Export receiver is running.")

    def log_message(self, format, *args):
        logger.debug(f"HTTP: {format % args}")

    def _process_health_data(self, data):
        conn = get_connection()
        hr_count = 0
        sleep_count = 0

        try:
            metrics = data.get("data", {}).get("metrics", [])

            for metric in metrics:
                name = metric.get("name", "")
                samples = metric.get("data", [])

                if name == "heart_rate":
                    for sample in samples:
                        timestamp = sample.get("date", "")
                        # Handle both "qty" (single value) and "Avg" (aggregated) formats
                        bpm = sample.get("qty") or sample.get("Avg")
                        if timestamp and bpm:
                            ts = _normalize_timestamp(timestamp)
                            conn.execute(
                                """INSERT OR IGNORE INTO heart_rate
                                   (timestamp, bpm, context, source)
                                   VALUES (?, ?, ?, ?)""",
                                (ts, int(float(bpm)), "resting", "apple"),
                            )
                            hr_count += 1

                elif name == "resting_heart_rate":
                    for sample in samples:
                        timestamp = sample.get("date", "")
                        bpm = sample.get("qty")
                        if timestamp and bpm:
                            ts = _normalize_timestamp(timestamp)
                            conn.execute(
                                """INSERT OR IGNORE INTO heart_rate
                                   (timestamp, bpm, context, source)
                                   VALUES (?, ?, ?, ?)""",
                                (ts, int(float(bpm)), "resting", "apple"),
                            )
                            hr_count += 1

                elif name == "heart_rate_variability":
                    for sample in samples:
                        timestamp = sample.get("date", "")
                        hrv_val = sample.get("qty")
                        if timestamp and hrv_val:
                            dt = _normalize_timestamp(timestamp)[:10]
                            conn.execute(
                                """INSERT OR REPLACE INTO wellness
                                   (date, hrv, source)
                                   VALUES (?, ?, ?)
                                   ON CONFLICT(date) DO UPDATE SET hrv = ?""",
                                (dt, float(hrv_val), "apple", float(hrv_val)),
                            )

                elif name == "sleep_analysis":
                    _process_sleep(samples, conn)
                    sleep_count += len(samples)

            conn.commit()
            logger.info(f"Received: {hr_count} HR samples, {sleep_count} sleep records")

        except Exception as e:
            logger.error(f"Error saving health data: {e}")
            raise
        finally:
            conn.close()


def _process_sleep(samples, conn):
    """Process sleep analysis samples into daily sleep records.

    Handles two formats:
    1. Aggregated (from Health Auto Export): {"date": "...", "totalSleep": 7.5, "core": 3.5, "deep": 1.5, "rem": 2.0, ...}
    2. Per-segment: {"value": "HKCategoryValueSleepAnalysisAsleepDeep", "date": "...", "endDate": "..."}
    """
    from collections import defaultdict

    for sample in samples:
        # Format 1: Aggregated sleep data (hours)
        if "totalSleep" in sample or "core" in sample or "deep" in sample:
            sleep_date = sample.get("date", "")[:10]
            if not sleep_date:
                continue

            total_hrs = sample.get("totalSleep") or sample.get("asleep") or 0
            deep_hrs = sample.get("deep", 0) or 0
            rem_hrs = sample.get("rem", 0) or 0
            core_hrs = sample.get("core", 0) or 0  # "core" = light sleep in Apple Health
            awake_hrs = sample.get("awake", 0) or 0

            conn.execute(
                """INSERT OR REPLACE INTO sleep
                   (date, total_minutes, deep_minutes, rem_minutes, light_minutes, awake_minutes, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    sleep_date,
                    round(total_hrs * 60, 1),
                    round(deep_hrs * 60, 1),
                    round(rem_hrs * 60, 1),
                    round(core_hrs * 60, 1),
                    round(awake_hrs * 60, 1),
                    "apple",
                ),
            )
            continue

        # Format 2: Per-segment sleep data (from raw export)
        value = sample.get("value", "")
        start_str = sample.get("date", "")
        end_str = sample.get("endDate", sample.get("end_date", ""))

        if not start_str or not end_str or not value:
            continue

        try:
            start = datetime.fromisoformat(start_str.replace(" ", "T").rstrip("Z"))
            end = datetime.fromisoformat(end_str.replace(" ", "T").rstrip("Z"))
            duration_mins = (end - start).total_seconds() / 60
        except ValueError:
            continue

        sleep_date = start.strftime("%Y-%m-%d")

        # Accumulate into a dict, then write at end
        if not hasattr(_process_sleep, "_nights"):
            _process_sleep._nights = defaultdict(lambda: {"total": 0, "deep": 0, "rem": 0, "light": 0, "awake": 0})

        nights = _process_sleep._nights

        if "AsleepDeep" in value:
            nights[sleep_date]["deep"] += duration_mins
            nights[sleep_date]["total"] += duration_mins
        elif "AsleepREM" in value:
            nights[sleep_date]["rem"] += duration_mins
            nights[sleep_date]["total"] += duration_mins
        elif "AsleepCore" in value or "AsleepUnspecified" in value:
            nights[sleep_date]["light"] += duration_mins
            nights[sleep_date]["total"] += duration_mins
        elif "Awake" in value:
            nights[sleep_date]["awake"] += duration_mins

    # Write per-segment data if any
    if hasattr(_process_sleep, "_nights"):
        for sleep_date, data in _process_sleep._nights.items():
            if data["total"] <= 0:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO sleep
                   (date, total_minutes, deep_minutes, rem_minutes, light_minutes, awake_minutes, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    sleep_date,
                    round(data["total"], 1),
                    round(data["deep"], 1),
                    round(data["rem"], 1),
                    round(data["light"], 1),
                    round(data["awake"], 1),
                    "apple",
                ),
            )
        _process_sleep._nights.clear()


def _normalize_timestamp(ts: str) -> str:
    """Normalize various timestamp formats to ISO format."""
    ts = ts.strip()
    # Handle "2026-04-04 08:00:00 -0700" format
    for fmt in ["%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"]:
        try:
            dt = datetime.strptime(ts, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    # Fallback: return as-is
    return ts


def run_server():
    """Start the HTTP server."""
    init_db()
    server = HTTPServer(("0.0.0.0", PORT), HealthDataHandler)
    logger.info(f"Apple Health receiver listening on port {PORT}")
    logger.info(f"Configure Health Auto Export to POST to: http://<your-mac-ip>:{PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped.")
        server.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    run_server()
