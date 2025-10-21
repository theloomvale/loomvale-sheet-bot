#!/usr/bin/env python3
import os
import re
import time
import unicodedata
from typing import List, Optional
from urllib.parse import urlparse

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# =========================
# ENV
# =========================
SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
GOOGLE_CX_ID = os.environ["GOOGLE_CX_ID"]
WORKSHEET_NAME: Optional[str] = None  # set your tab name if not the first sheet

# =========================
# COLUMN HEADERS (fixed)
# =========================
# A Status | B Topic | C ImageSource | D ImagePrompt | E SourceLinks | F Tone |
# G CaptionPrompt | H HashtagPrompt | I FinalImage | J AI Image Links | K Assistant
H_STATUS = "Status"
H_TOPIC = "Topic"
H_SOURCE = "ImageSource"
H_PROMPT = "ImagePrompt"
H_LINKS = "SourceLinks"
H_TONE = "Tone"
H_CAPTION = "CaptionPrompt"
H_HASHTAG = "HashtagPrompt"
H_FINAL = "FinalImage"
H_AI_LINKS = "AI Image Links"
H_ASSIST = "Assistant"

ALLOWED_WRITE_COLS = {H_PROMPT, H_LINKS, H_TONE, H_CAPTION, H_HASHTAG, H_ASSIST}

# =========================
# BEHAVIOR TUNING
# =========================
MAX_ROWS_PER_RUN = 120
PER_QUERY_SLEEP = 0.7
WRITE_SLEEP = 0.15
RETRY_COUNT = 3

# For link rows: prefer official/reliable domains
PREFERRED_DOMAINS = {
    "crunchyroll.com", "ghibli.jp", "aniplex.co.jp", "toho.co.jp", "imdb.com",
    "netflix.com", "fuji.tv", "kadokawa.co.jp", "sega.jp", "bandainamcoent.co.jp",
    "posterdb.com", "theposterdb.com", "horrorsociety.com", "toei.co.jp",
    "viz.com", "animatetimes.com", "mantan-web.jp", "dengekionline.com"
}
EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp)(?:$|\?)", re.IGNORECASE)

# Brand colors for AI prompts
BRAND_COLORS = [
    "Mizu blue", "Soft sage green", "War lantern orange", "Karma beige", "Charcoal gray"
]

# =========================
# SHEETS HELPERS
# =========================
def get_ws():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not creds_path or not os.path.exists(creds_path):
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS must point to credentials.json")
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    return sh.worksheet(WORKSHEET_NAME) if WORKSHEET_NAME else sh.sheet1


def header_map(ws) -> dict:
    headers = [h.strip() for h in ws.row_values(1)]
    mapping = {h: i + 1 for i, h in enumerate(headers)}
    # sanity
    for needed in [H_STATUS, H_TOPIC, H_SOURCE, H_PROMPT, H_LINKS, H_TONE, H_CAPTION, H_HASHTAG, H_FINAL, H_AI_LINKS, H_ASSIST]:
        if needed not in mapping:
            raise RuntimeError(f"Missing header '{needed}'. Found: {headers}")
    return mapping


# =========================
# TONE / PROMPT HELPERS
# =========================
def normalize(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def infer_tone(topic: str) -> str:
    t = normalize(topic)
    shonen = ["attack on titan", "aot", "naruto", "bleach", "jujutsu", "demon slayer", "one piece", "trigun", "cyberpunk", "blue exorcist", "hell's paradise", "spy x family"]
    cozy = ["ghibli", "shinkai", "your name", "weathering with you", "princess mononoke", "totoro", "nausicaa", "slice of life", "beastars"]
    tech = ["ai", "design", "ux", "ui", "ar", "vr", "3d", "tooling", "midjourney", "runway"]
    romance = ["romance", "love", "feelings", "kimi ni todoke", "horimiya", "fruits basket"]

    if any(k in t for k in shonen):
        return "Dramatic, bold with emotional depth"
    if any(k in t for k in cozy):
        return "Nostalgic, cozy, empathic"
    if any(k in t for k in tech):
        return "Informative, cozy-tech, empathic"
    if any(k in t for k in romance):
        return "Tender, poetic, heartfelt"
    return "Cozy, empathic"


def caption_prompt(topic: str, tone: str) -> str:
    return (
        f"Write an Instagram caption about: {topic}.\n"
        f"Tone: {tone}.\n"
        "Loomvale’s cozy-empathic voice, cinematic warmth, emotional nuance.\n"
        "Start with a short emotional hook, then 2–3 concise lines, end with a subtle CTA (\"save for later\").\n"
        "Max 300 chars, ≤2 emojis, no hashtags."
    )


def hashtag_prompt(topic: str) -> str:
    # if topic suggests tech/design
    if any(k in normalize(topic) for k in ["ai", "design", "ux", "ui", "ar", "vr", "3d", "tool"]):
        return "20 lowercase keyword tags (comma-separated, no #). Focus on tech/ai/design terms and aesthetics."
    return "10 hashtags with # included, space-separated, mixing franchise, genre, and aesthetic."


def ai_image_prompt(topic: str) -> str:
    color = BRAND_COLORS[int(time.time()) % len(BRAND_COLORS)]
    return (
        f"Create 5 cinematic images for: {topic}. Color theme: {color}.\n"
        "Loomvale art direction: lo-fi, painterly, soft film-grain texture; soft, cinematic warmth; "
        "East Asian character type; manga-style dialogue with gray handwritten narration; "
        "text integrated naturally into the scene. Emphasize atmosphere, subtle motion, and cozy intimacy."
    )


# =========================
# IMAGE SEARCH
# =========================
def is_portrait(item: dict) -> bool:
    info = item.get("image") or {}
    try:
        w, h = int(info.get("width", 0)), int(info.get("height", 0))
        return h > w and h >= 600  # rough portrait & reasonable size
    except Exception:
        return False


def is_allowed_link(url: str) -> bool:
    # extension check
    if not EXT_RE.search(url):
        return False
    # domain check
    try:
        host = urlparse(url).netloc.lower()
        host = host.split(":")[0]
        # allow subdomains of preferred domains
        return any(host == d or host.endswith("." + d) for d in PREFERRED_DOMAINS)
    except Exception:
        return False


def search_images(topic: str) -> List[str]:
    service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
    queries = [
        f"{topic} official key visual poster",
        f"{topic} key visual portrait",
        f"{topic} anime poster official",
        f"{topic}"
    ]
    seen, best = set(), []
    for q in queries:
        for attempt in range(RETRY_COUNT):
            try:
                resp = service.cse().list(
                    q=q, cx=GOOGLE_CX_ID, searchType="image", num=10, safe="active"
                ).execute()
                items = resp.get("items", []) or []
                # Filter portrait + allowed domain + extension
                for it in items:
                    link = it.get("link")
                    if not link or link in seen:
                        continue
                    if is_portrait(it) and is_allowed_link(link):
                        best.append(link)
                        seen.add(link)
                        if len(best) >= 3:
                            return best[:3]
                break
            except HttpError as e:
                if getattr(e, "resp", None) and e.resp.status in (429, 500, 503):
                    time.sleep(1.2 + attempt)
                    continue
                raise
            except Exception:
                if attempt == RETRY_COUNT - 1:
                    raise
                time.sleep(0.8 + attempt)
        time.sleep(PER_QUERY_SLEEP)
    return best[:3]


# =========================
# NEW TOPIC DISCOVERY
# =========================
SEED_TOPICS = [
    "Scarlett (2025) — retro-futurist city night key visual",
    "Cozy manga atelier — brush textures & warm desk light",
    "Shibuya alley rain scene — neon reflections",
    "Ghibli-inspired forest spirits — dawn mist",
    "Retro arcade memories — pixel glow & soft film grain",
    "Urban shrine at blue hour — paper lanterns & wind",
    "Kintsugi-inspired poster — gold repair, charcoal gray",
    "Late-night anime study desk — notes & cassette player",
    "Train window monologue — moving lights, quiet thoughts",
    "Cyberpunk hanami — sakura under city skylines",
]

def append_new_idea_rows(ws, hdr: dict, n: int = 5):
    # Append N new idea rows as per spec:
    # A: Ready | B: New topic | C: Link | F: Auto tone | G: Caption | H: Hashtag | K: To do
    added = 0
    for topic in SEED_TOPICS:
        if added >= n:
            break
        tone = infer_tone(topic)
        cap = caption_prompt(topic, tone)
        tags = hashtag_prompt(topic)
        row = [""] * len(hdr)  # we'll place by header positions to be safe
        # But we cannot rely on header order for append_row; build in schema order A..K directly:
        values = [
            "Ready",               # A Status
            topic,                 # B Topic
            "Link",                # C ImageSource
            "",                    # D ImagePrompt
            "",                    # E SourceLinks
            tone,                  # F Tone
            cap,                   # G CaptionPrompt
            tags,                  # H HashtagPrompt
            "",                    # I FinalImage
            "",                    # J AI Image Links
            "To do"                # K Assistant
        ]
        ws.append_row(values, value_input_option="RAW")
        added += 1
        time.sleep(WRITE_SLEEP)
    if added:
        print(f"🆕 Appended {added} new idea rows.")


# =========================
# MAIN
# =========================
def row_is_fully_empty(row: List[str], hdr: dict) -> bool:
    # Fully empty meaning B..K are empty (ignore A per rule)
    b_to_k_idxs = [hdr[H_TOPIC], hdr[H_SOURCE], hdr[H_PROMPT], hdr[H_LINKS], hdr[H_TONE],
                   hdr[H_CAPTION], hdr[H_HASHTAG], hdr[H_FINAL], hdr[H_AI_LINKS], hdr[H_ASSIST]]
    for idx in b_to_k_idxs:
        val = row[idx - 1] if len(row) >= idx else ""
        if str(val).strip():
            return False
    return True


def process():
    ws = get_ws()
    hdr = header_map(ws)
    rows = ws.get_all_values()[1:]  # skip header

    updated = 0
    empties_detected = 0

    for r_idx, row in enumerate(rows, start=2):
        try:
            # Skip if Assistant == Done
            assistant = (row[hdr[H_ASSIST] - 1] if len(row) >= hdr[H_ASSIST] else "").strip()
            if assistant == "Done":
                continue

            # Detect fully empty rows (B..K empty) — do not modify them; we will append new rows instead later
            if row_is_fully_empty(row, hdr):
                empties_detected += 1
                continue

            topic = (row[hdr[H_TOPIC] - 1] if len(row) >= hdr[H_TOPIC] else "").strip()
            source = (row[hdr[H_SOURCE] - 1] if len(row) >= hdr[H_SOURCE] else "").strip()

            # We NEVER modify A (Status), B (Topic), C (ImageSource), or J (AI Image Links)
            # We ONLY write to D, E, F, G, H, K

            if source.lower() == "link":
                if not topic:
                    continue
                tone = infer_tone(topic)
                links = search_images(topic)
                # Fill E/F/G/H; set K per rules
                ws.update_cell(r_idx, hdr[H_TONE], tone)
                ws.update_cell(r_idx, hdr[H_CAPTION], caption_prompt(topic, tone))
                ws.update_cell(r_idx, hdr[H_HASHTAG], hashtag_prompt(topic))
                status_k = "Done" if len(links) == 3 else "Needs Images"
                # Only write E if we found anything; leave it untouched otherwise
                if links:
                    ws.update_cell(r_idx, hdr[H_LINKS], ", ".join(links))
                ws.update_cell(r_idx, hdr[H_ASSIST], status_k)
                updated += 1
                print(f"🔗 Row {r_idx} | {topic} → {status_k} ({len(links)} links)")
                time.sleep(WRITE_SLEEP)

            elif source.lower() == "ai":
                if not topic:
                    continue
                tone = infer_tone(topic)
                ws.update_cell(r_idx, hdr[H_PROMPT], ai_image_prompt(topic))
                ws.update_cell(r_idx, hdr[H_TONE], tone)
                ws.update_cell(r_idx, hdr[H_CAPTION], caption_prompt(topic, tone))
                ws.update_cell(r_idx, hdr[H_HASHTAG], hashtag_prompt(topic))
                ws.update_cell(r_idx, hdr[H_ASSIST], "Done")
                updated += 1
                print(f"🤖 Row {r_idx} | {topic} → AI prompt written + Done")
                time.sleep(WRITE_SLEEP)

            else:
                # Unknown source value; we keep columns untouched per rules.
                continue

            if updated >= MAX_ROWS_PER_RUN:
                print(f"ℹ️ Reached MAX_ROWS_PER_RUN={MAX_ROWS_PER_RUN}.")
                break

        except Exception as e:
            print(f"❌ Error on row {r_idx}: {e}")

    # After scanning, if we saw any fully empty rows, append fresh idea rows
    if empties_detected:
        append_new_idea_rows(ws, hdr, n=min(empties_detected, 5))

    print(f"Done. Updated rows: {updated}. Empty rows spotted: {empties_detected}")


if __name__ == "__main__":
    process()
