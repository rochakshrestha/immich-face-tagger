#!/usr/bin/env python3
import logging
import os
import time
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

TARGET_PERSON_ID = os.getenv("TARGET_PERSON_ID", "").strip()
SHARED_ALBUM_ID = os.getenv("SHARED_ALBUM_ID", "").strip()
UNARCHIVE_SHARED_ASSETS = os.getenv("UNARCHIVE_SHARED_ASSETS", "false").lower() in ("1", "true", "yes")
ARCHIVE_SHARED_ASSETS = os.getenv("ARCHIVE_SHARED_ASSETS", "false").lower() in ("1", "true", "yes")

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
        log.error("%s %s -> %s %s", method, path, r.status_code, r.text[:300])
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


def search_recent_assets(since: datetime, person_ids: list[str] | None = None):
    body = {
        "createdAfter": since.isoformat().replace("+00:00", "Z"),
        "size": BATCH_SIZE,
    }
    if person_ids:
        body["personIds"] = person_ids
    result = api("POST", "/search/metadata", json=body).json()
    return result.get("assets", {}).get("items", [])


def asset_has_faces(asset_id: str) -> bool:
    faces = api("GET", f"/faces?id={asset_id}").json()
    return len(faces) > 0


def apply_tag(asset_ids: list[str], tag_id: str):
    if not asset_ids:
        return
    api("PUT", "/tags/assets", json={
        "tagIds": [tag_id],
        "assetIds": asset_ids,
    })
    log.info("Tagged %d asset(s) with '%s'", len(asset_ids), TARGET_TAG_NAME)


def get_existing_album_asset_ids(album_id: str) -> set[str]:
    album = api("GET", f"/albums/{album_id}").json()
    return {a["id"] for a in album.get("assets", []) if "id" in a}


def set_asset_archived(asset_id: str, archived: bool):
    api("PUT", f"/assets/{asset_id}", json={"isArchived": archived})


def sync_person_to_shared_album(since: datetime):
    if not TARGET_PERSON_ID or not SHARED_ALBUM_ID:
        return

    matched_assets = search_recent_assets(since, person_ids=[TARGET_PERSON_ID])
    if not matched_assets:
        return

    existing_ids = get_existing_album_asset_ids(SHARED_ALBUM_ID)
    new_assets = [a for a in matched_assets if a.get("id") and a["id"] not in existing_ids]
    if not new_assets:
        return

    ids_to_add = []
    for asset in new_assets:
        asset_id = asset["id"]
        if UNARCHIVE_SHARED_ASSETS and not ARCHIVE_SHARED_ASSETS and asset.get("isArchived"):
            try:
                set_asset_archived(asset_id, False)
                log.info("Unarchived asset %s before adding to shared album", asset_id)
            except Exception as e:
                log.warning("Could not unarchive %s: %s", asset_id, e)
        ids_to_add.append(asset_id)

    if not ids_to_add:
        return

    api("PUT", f"/albums/{SHARED_ALBUM_ID}/assets", json={"ids": ids_to_add})
    log.info("Added %d asset(s) for person %s to album %s", len(ids_to_add), TARGET_PERSON_ID, SHARED_ALBUM_ID)

    if ARCHIVE_SHARED_ASSETS:
        for asset_id in ids_to_add:
            try:
                set_asset_archived(asset_id, True)
                log.info("Archived asset %s after adding to shared album", asset_id)
            except Exception as e:
                log.warning("Could not archive %s: %s", asset_id, e)


def process_once(tag_id: str):
    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    log.info("Scanning assets created after %s", since.isoformat())

    assets = search_recent_assets(since)
    log.info("Found %d candidate asset(s)", len(assets))

    to_tag = []
    for asset in assets:
        asset_id = asset["id"]

        existing = {t["id"] for t in asset.get("tags", [])}
        if tag_id in existing:
            continue

        try:
            if asset_has_faces(asset_id):
                to_tag.append(asset_id)
        except Exception as e:
            log.warning("Could not check faces for %s: %s", asset_id, e)

    apply_tag(to_tag, tag_id)

    try:
        sync_person_to_shared_album(since)
    except Exception as e:
        log.warning("Could not sync person %s to album %s: %s", TARGET_PERSON_ID, SHARED_ALBUM_ID, e)


def main():
    log.info("Starting face tagger (poll every %ds)", POLL_INTERVAL)
    if (TARGET_PERSON_ID and not SHARED_ALBUM_ID) or (SHARED_ALBUM_ID and not TARGET_PERSON_ID):
        log.warning("Both TARGET_PERSON_ID and SHARED_ALBUM_ID are required for auto-album sync")

    tag_id = get_or_create_tag(TARGET_TAG_NAME)

    while True:
        try:
            process_once(tag_id)
        except Exception as e:
            log.exception("Error: %s", e)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
