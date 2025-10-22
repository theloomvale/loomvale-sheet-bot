#!/usr/bin/env python3
import os
import re
import time
import random
import requests
import unicodedata
from urllib.parse import urlparse
from typing import List, Optional
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from huggingface_hub import HfApi

# ======================================
# ENVIRONMENT VARIABLES
# ======================================
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
SHEET_ID = os.getenv("SHEET_ID")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX_ID = os.getenv("GOOGLE_CX_ID")
HF_TOKEN = os.getenv("HF_TOKEN")
HF_SPACE_URL = os.getenv("HF_SPACE_URL")
PIPELINE_TAB = os.getenv("PIPELINE_TAB", "Pipeline")

# ======================================
# COLUMN HEADERS (NEW STRUCTURE)
# ======================================
# A Status | B Topic | C ImageSource | D SourceLinks | E ImagePrompt_Ambience |
# F ImagePrompt_Scenes | G Tone | H Caption+Hashtags | I Assistant
H_STATUS = "Status"
H_TOPIC = "Topic"
H_SOURCE = "ImageSource"
H_LINKS = "SourceLinks"
H_AMBIENCE = "ImagePrompt_Ambience"
H_SCENES = "ImagePrompt_Scenes"
H_TONE = "Tone"
H_CAPTION = "Caption+Hashtags Prompt"
H_ASSIST = "Assistant"

MAX_ROWS_PER_RUN = 5
PER_QUERY_SLEEP = 0.8
WRITE_SLEEP = 0.2
RETRY_COUNT = 3

# ======================================
# IMAGE SEARCH CONFIG
# ======================================
PREFERRED_DOMAINS = {
    "crunchyroll.com", "ghibli.jp", "aniplex.co.jp", "toho.co.jp", "imdb.com",
    "media-amazon.com", "storyblok.com", "theposterdb.com", "viz.com",
    "myanimelist.net", "netflix.com", "bandainamcoent.co.jp", "fuji.tv",
    "toei-anim.co.jp", "kadokawa.co.jp", "shueisha.co.jp",
    "avex.co.jp", "aniverse-mag.com", "eiga.com", "natalie.mu", "animenewsnetwork.com",
    "pinterest.com", "pinimg.com"
}
EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp)(?:$|\?)", re.IGNORECASE)

# ======================================
# GOOGLE SHEETS HELPERS
# ======================================
def get_ws():
    """Return worksheet; default to first sheet if tab not found."""
    creds = Credentials.from_service_account_info(
        eval(GOOGLE_CREDENTIALS_JSON),
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        return sh.worksheet(PIPELINE_TAB)
    except Exception:
        return sh.sheet1

def header_map(ws):
    headers = [h.strip() for h in ws.row_values(1)]
    mapping = {h: i + 1 for i, h in enumerate(headers)}
    for col in [H_STATUS, H_TOPIC, H_SOURCE, H_LINKS, H_AMBIENCE, H_SCENES, H_TONE, H_CAPTION, H_ASSIST]:
        if col not in mapping:
            raise RuntimeError(f"Missing header: {col}")
    return mapping

# ======================================
# TONE + PROMPT HELPERS
# ======================================
def normalize(s): return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()

def infer_tone(topic):
    t = normalize(topic)
    if any(k in t for k in ["attack", "fight", "battle", "hero", "demon", "bleach", "jujutsu", "trigun", "cyberpunk", "spy x family"]):
        return "Dramatic, bold with emotional depth"
    if any(k in t for k in ["ghibli", "shinkai", "slice", "totoro", "weathering", "cozy", "studio"]):
        return "Nostalgic, cozy, empathic"
    if any(k in t for k in ["ai", "design", "vr", "tech", "ux", "cyber", "hologram", "future"]):
        return "Informative, cozy-tech, empathic"
    if any(k in t for k in ["romance", "love", "feelings", "goodbye", "memory"]):
        return "Tender, poetic, heartfelt"
    return "Cozy, empathic"

def caption_prompt(topic, tone):
    return (
        f"Write an Instagram caption about: {topic}. Tone: {tone}. "
        "Use Loomvale’s cinematic, cozy-empathic style. Begin with a short emotional hook, "
        "then two to three concise sentences, ending with a subtle call to action like “save for later.” "
        "Maximum 600 characters, no more than two emojis. Then write 10 hashtags related to the topic, each with # and space-separated."
    )

# ======================================
# AI PROMPT HELPERS
# ======================================
BRAND_COLORS = [
    "Mizu blue", "Soft sage green", "War lantern orange", "Karma beige", "Charcoal gray"
]

ARCHETYPES = {
    "cozy": ["lofi", "cat", "desk", "room", "study", "cafe", "nap"],
    "fantasy": ["forest", "spirit", "shrine", "sky", "wind"],
    "urban": ["city", "train", "station", "night", "neon"],
    "romance": ["love", "goodbye", "memory", "letter", "walk"],
    "tech": ["ai", "robot", "cyber", "design", "studio"]
}

def pick_color(topic):
    idx = abs(hash(topic)) % len(BRAND_COLORS)
    return BRAND_COLORS[idx]

def infer_archetype(topic):
    t = normalize(topic)
    for arch, kws in ARCHETYPES.items():
        if any(k in t for k in kws): return arch
    return "cozy"

def ai_image_prompt(topic):
    color = pick_color(topic)
    arche = infer_archetype(topic)
    ambience = (
        f"(Color Theme: {color}) Lo-fi, painterly, soft film-grain texture, cinematic warmth, "
        f"East Asian character type, stylized dialogue bubbles (blank), soft hand-drawn panel captions, "
        f"faint gray handwritten shapes (not readable). Ambience style: {arche}."
    )

    scenes = []
    for i in range(1, 6):
        visual = f"Scene {i} Visual: {random.choice(['soft rain', 'glowing desk light', 'quiet alley', 'distant skyline', 'reflected neon'])} — fitting the {arche} theme."
        mood = f"Scene {i} Mood: {random.choice(['gentle', 'nostalgic', 'dreamlike', 'introspective', 'melancholic'])}."
        text = f"Scene {i} Text: (manga font): \"{random.choice(['five more minutes', 'see you', 'it’s late', 'thank you', 'tomorrow again'])}\" (handwritten gray) emotional tone in air."
        scenes.append(f"{visual}\n{mood}\n{text}")
    return ambience, "\n\n".join(scenes)

# ======================================
# IMAGE SEARCH
# ======================================
def is_portrait(item):
    info = item.get("image", {})
    try:
        w, h = int(info.get("width", 0)), int(info.get("height", 0))
        return h > w and h >= 800
    except Exception:
        return False

def is_allowed(url):
    if not EXT_RE.search(url): return False
    host = urlparse(url).netloc.lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in PREFERRED_DOMAINS)

def search_images(topic):
    if not GOOGLE_API_KEY or not GOOGLE_CX_ID:
        return []
    service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
    queries = [
        f"{topic} official key visual poster",
        f"{topic} anime visual portrait",
        f"{topic} promotional image site:crunchyroll.com OR site:ghibli.jp OR site:imdb.com OR site:pinterest.com",
    ]
    seen, results = set(), []
    for q in queries:
        try:
            resp = service.cse().list(q=q, cx=GOOGLE_CX_ID, searchType="image", num=10, safe="active").execute()
            for item in resp.get("items", []):
                link = item.get("link")
                if link and is_portrait(item) and is_allowed(link) and link not in seen:
                    results.append(link)
                    seen.add(link)
                    if len(results) >= 3:
                        return results
        except HttpError:
            continue
        time.sleep(PER_QUERY_SLEEP)
    return results

# ======================================
# HUGGING FACE PING
# ======================================
def ping_huggingface_space():
    try:
        if not HF_TOKEN or not HF_SPACE_URL:
            print("⚠️ Hugging Face credentials missing, skipping ping.")
            return
        api = HfApi(token=HF_TOKEN)
        space_id = HF_SPACE_URL.replace("https://huggingface.co/spaces/", "")
        api.restart_space(space_id)
        print(f"🔄 Restarted Hugging Face Space: {HF_SPACE_URL}")
    except Exception as e:
        print(f"⚠️ Could not restart Hugging Face Space: {e}")

# ======================================
# MAIN PROCESS LOOP
# ======================================
def process():
    ws = get_ws()
    hdr = header_map(ws)
    rows = ws.get_all_values()[1:]
    updated = 0

    for i, row in enumerate(rows, start=2):
        try:
            status = (row[hdr[H_STATUS]-1]).strip() if len(row) >= hdr[H_STATUS] else ""
            topic = (row[hdr[H_TOPIC]-1]).strip() if len(row) >= hdr[H_TOPIC] else ""
            source = (row[hdr[H_SOURCE]-1]).strip().lower() if len(row) >= hdr[H_SOURCE] else ""
            assist = (row[hdr[H_ASSIST]-1]).strip() if len(row) >= hdr[H_ASSIST] else ""

            if not topic or assist == "Done": continue

            tone = infer_tone(topic)
            ws.update_cell(i, hdr[H_TONE], tone)

            if source == "link":
                links = search_images(topic)
                ws.update_cell(i, hdr[H_LINKS], ", ".join(links) if links else "Couldn't find images")
                ws.update_cell(i, hdr[H_CAPTION], caption_prompt(topic, tone))
                ws.update_cell(i, hdr[H_ASSIST], "Done" if len(links) == 3 else "Couldn't find images")

            elif source == "ai":
                ambience, scenes = ai_image_prompt(topic)
                ws.update_cell(i, hdr[H_AMBIENCE], ambience)
                ws.update_cell(i, hdr[H_SCENES], scenes)
                ws.update_cell(i, hdr[H_CAPTION], caption_prompt(topic, tone))
                ws.update_cell(i, hdr[H_ASSIST], "Done")

            updated += 1
            if updated >= MAX_ROWS_PER_RUN: break
            time.sleep(WRITE_SLEEP)
        except Exception as e:
            print(f"❌ Row {i} error: {e}")
            ws.update_cell(i, hdr[H_ASSIST], "Error")

    ping_huggingface_space()
    print(f"✅ Updated {updated} rows successfully.")

if __name__ == "__main__":
    process()
