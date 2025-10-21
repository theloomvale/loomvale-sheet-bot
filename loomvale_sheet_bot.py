#!/usr/bin/env python3
import os
import time
import unicodedata
from typing import List, Optional

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# =========================
# ENV (from GitHub Secrets)
# =========================
SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
GOOGLE_CX_ID = os.environ["GOOGLE_CX_ID"]
# If you have multiple tabs, set this to the tab name. Otherwise leave as None.
WORKSHEET_NAME: Optional[str] = None

# =========================
# Sheet headers (exact text)
# =========================
HEADER_STATUS = "Status"
HEADER_TOPIC = "Topic"

# Your pipeline columns (as you described):
# Status | Topic | ImageSource | ImagePrompt | SourceLinks | Tone | CaptionPrompt | HashtagPrompt | FinalImage | AI Image Links | Assistant
# We will primarily fill ImageSource. If "AI Image Links" also exists, we mirror into it.
HEADER_IMAGE_SOURCE_PRIMARY = "ImageSource"
HEADER_IMAGE_SOURCE_ALIASES = ["Imagesources", "Image Sources", "Image Source"]  # safety
HEADER_AI_LINKS = "AI Image Links"  # optional mirror

# Behavior
MAX_ROWS_PER_RUN = 100
RETRY_COUNT = 3
PER_QUERY_SLEEP_SECONDS = 0.8
WRITE_SLEEP_SECONDS = 0.2


# ----------------------------
# Google Sheets helpers
# ----------------------------
def get_ws():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path or not os.path.exists(creds_path):
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS must point to credentials.json")
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    return sh.worksheet(WORKSHEET_NAME) if WORKSHEET_NAME else sh.sheet1


def build_header_map(ws) -> dict:
    headers = ws.row_values(1)
    mapping = {h.strip(): i + 1 for i, h in enumerate(headers)}

    # Find ImageSource with aliases if needed
    image_source_col = mapping.get(HEADER_IMAGE_SOURCE_PRIMARY)
    if not image_source_col:
        for alias in HEADER_IMAGE_SOURCE_ALIASES:
            if alias in mapping:
                image_source_col = mapping[alias]
                break

    missing = []
    if HEADER_STATUS not in mapping:
        missing.append(HEADER_STATUS)
    if HEADER_TOPIC not in mapping:
        missing.append(HEADER_TOPIC)
    if not image_source_col:
        missing.append(f"{HEADER_IMAGE_SOURCE_PRIMARY} (or one of {HEADER_IMAGE_SOURCE_ALIASES})")

    if missing:
        raise RuntimeError(f"Missing required header(s): {', '.join(missing)}. Found: {list(mapping.keys())}")

    return {
        "status_col": mapping[HEADER_STATUS],
        "topic_col": mapping[HEADER_TOPIC],
        "image_source_col": image_source_col,
        "ai_links_col": mapping.get(HEADER_AI_LINKS)  # optional
    }


# ----------------------------
# Image search via Google CSE
# ----------------------------
def find_top3_images(query: str, api_key: str, cx: str) -> List[str]:
    service = build("customsearch", "v1", developerKey=api_key)
    for attempt in range(RETRY_COUNT):
        try:
            resp = service.cse().list(
                q=query,
                cx=cx,
                searchType="image",
                num=10,
                safe="active",
            ).execute()
            items = resp.get("items", []) or []
            seen, links = set(), []
            for it in items:
                link = it.get("link")
                if link and link not in seen:
                    seen.add(link)
                    links.append(link)
                if len(links) == 3:
                    break
            return links
        except HttpError as e:
            # Handle transient quota/server errors
            if getattr(e, "resp", None) and e.resp.status in (429, 500, 503):
                time.sleep(1.5 + attempt)
                continue
            raise
        except Exception:
            if attempt == RETRY_COUNT - 1:
                raise
            time.sleep(1 + attempt)
    return []


def strip_diacritics(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


# ----------------------------
# Core
# ----------------------------
def process_sheet():
    ws = get_ws()
    cols = build_header_map(ws)

    status_col = cols["status_col"]
    topic_col = cols["topic_col"]
    image_source_col = cols["image_source_col"]
    ai_links_col = cols.get("ai_links_col")

    all_values = ws.get_all_values()
    rows = all_values[1:]  # skip header

    updated = 0
    for idx, row in enumerate(rows, start=2):
        try:
            status = (row[status_col - 1] if len(row) >= status_col else "").strip()
            topic = (row[topic_col - 1] if len(row) >= topic_col else "").strip()
            existing_img_src = (row[image_source_col - 1] if len(row) >= image_source_col else "").strip()

            if status != "Ready":
                continue
            if not topic:
                continue
            if existing_img_src:
                # Already filled; skip
                continue

            # Query variants to improve hit rate
            queries = [
                f"{topic} key visual",
                f"{topic} poster",
                f"{topic} official visual",
                strip_diacritics(topic),
            ]

            links: List[str] = []
            for q in queries:
                time.sleep(PER_QUERY_SLEEP_SECONDS)
                links = find_top3_images(q, GOOGLE_API_KEY, GOOGLE_CX_ID)
                if len(links) >= 3:
                    break

            if not links:
                print(f"⚠️  No images found for row {idx}: {topic}")
                continue

            joined = ", ".join(links[:3])

            # Write primary ImageSource
            ws.update_cell(idx, image_source_col, joined)
            # Mirror to AI Image Links if that column exists
            if ai_links_col:
                ws.update_cell(idx, ai_links_col, joined)

            updated += 1
            print(f"✅ Row {idx} | {topic} → {joined}")
            time.sleep(WRITE_SLEEP_SECONDS)

            if updated >= MAX_ROWS_PER_RUN:
                print(f"ℹ️ Reached MAX_ROWS_PER_RUN={MAX_ROWS_PER_RUN}. Stopping.")
                break

        except Exception as e:
            print(f"❌ Error on row {idx}: {e}")

    print(f"Done. Updated rows: {updated}")


if __name__ == "__main__":
    process_sheet()
