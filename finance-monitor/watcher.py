"""
Scan the intake/ folder for new YNAB CSV exports and PDF documents.
Imports each file, then moves it to imported/YYYY-MM/.
Runs once and exits (invoked by a 5-minute interval LaunchAgent).
"""
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
import db
from ingest import pdf_importer, ynab_api, ynab_csv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def _dest_dir() -> Path:
    month = datetime.now(tz=timezone.utc).strftime("%Y-%m")
    d = config.IMPORTED_DIR / month
    d.mkdir(parents=True, exist_ok=True)
    return d


def run() -> None:
    db.init_db()
    config.INTAKE_DIR.mkdir(parents=True, exist_ok=True)

    status = ynab_api.sync()
    logger.info("watcher: ynab sync → %s", status)

    files = list(config.INTAKE_DIR.iterdir())
    if not files:
        logger.info("watcher: intake/ is empty — nothing to do")
        return

    dest = _dest_dir()

    for path in files:
        if path.name.startswith("."):
            continue

        suffix = path.suffix.lower()

        if suffix == ".csv":
            logger.info("watcher: importing YNAB CSV: %s", path.name)
            imported, skipped = ynab_csv.import_file(path)
            logger.info("watcher: %s → %d imported, %d skipped", path.name, imported, skipped)
            shutil.move(str(path), dest / path.name)

        elif suffix == ".pdf":
            logger.info("watcher: importing PDF: %s", path.name)
            ok = pdf_importer.import_file(path)
            if ok:
                shutil.move(str(path), dest / path.name)
            else:
                logger.info("watcher: %s already imported — moving anyway", path.name)
                shutil.move(str(path), dest / path.name)

        else:
            logger.warning("watcher: unrecognised file type %s — skipping", path.name)
