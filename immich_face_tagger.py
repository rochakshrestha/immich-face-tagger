#!/usr/bin/env python3
import os
import time
import logging
from datetime import datetime, timedelta, timezone

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("immich-face-tagger")

IMMICH_URL = os.getenv("IMMICH_URL", "http://immich-server:2283/api").rstrip("/")
API_KEY = os.getenv("IMMICH_API_KEY")
TARGET_TAG_NAME = os.getenv("TARGET_TAG_NAME", "has-person")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))      # 5 minutes
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "48"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))

if not API_KEY:
    raise SystemExit("IMMICH_API_KEY is required")

headers = {
    "x-api-key": API_KEY,
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def api(method, path, **kwargs):
    url = f"{IMMICH_URL}{path}"
    r = requests.request(method, url, headers=headers, timeout=30, **kwargs)
    if r.status_code >= 400:
        log.error("%s %s → %s %s", method, path, r.status_code, r.text[:300])
        r.raise_for_status()
    return r


def get_or_create_tag(name: str) -> str:
    tags = api("GET", "/tags").json()
    for t in tags:
        if t.get("name") == name:
            return t["id"]

    log.info("Creating tag '%s'", name)
    created = api("POST", "/tags", json={"name": name}).json()
    return created["id"]


def search_recent_assets(since: datetime):
    body = {
        "createdAfter": since.isoformat().replace("+00:00", "Z"),
        "size": BATCH_SIZE,
    }
    result = api("POST", "/search/metadata", json=body).json()
    return result.get("assets", {}).get("items", [])


def asset_has_faces(asset_id: str) -> bool:
    faces = api("GET", f"/faces?assetId={asset_id}").json()
    return len(faces) > 0


def apply_tag(asset_ids: list[str], tag_id: str):
    if not asset_ids:
        return
    api("PUT", "/tags/assets", json={
        "tagIds": [tag_id],
        "assetIds": asset_ids,
    })
    log.info("Tagged %d asset(s) with '%s'", len(asset_ids), TARGET_TAG_NAME)


def process_once(tag_id: str):
    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    log.info("Scanning assets created after %s", since.isoformat())

    assets = search_recent_assets(since)
    log.info("Found %d candidate asset(s)", len(assets))

    to_tag = []
    for asset in assets:
        asset_id = asset["id"]

        # Skip if already tagged
        existing = {t["id"] for t in asset.get("tags", [])}
        if tag_id in existing:
            continue

        try:
            if asset_has_faces(asset_id):
                to_tag.append(asset_id)
        except Exception as e:
            log.warning("Could not check faces for %s: %s", asset_id, e)

    apply_tag(to_tag, tag_id)


def main():
    log.info("Starting face tagger (poll every %ds)", POLL_INTERVAL)
    tag_id = get_or_create_tag(TARGET_TAG_NAME)

    while True:
        try:
            process_once(tag_id)
        except Exception as e:
            log.exception("Error: %s", e)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
