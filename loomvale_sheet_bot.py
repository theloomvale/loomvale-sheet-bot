#!/usr/bin/env python3
"""
Loomvale Sheet Bot (new layout)

Columns (A→J):
A Status
B Topic
C ImageSource                 ("AI" or "Link", case/space-insensitive)
D SourceLinks                 (for Link rows: up to 3 portrait URLs)
E ImagePrompt_Ambience        (directions-only, written by Assistant)
F ImagePrompt_Scenes          (directions-only, written by Assistant)
G AI generated images         (5 URLs written by this bot after HF run)
H Tone
I Caption+Hashtags Prompt     (merged prompt: caption + hashtag rules)
J Assistant                   (state machine)

States we set in J (Assistant):
- "Needs Prompts"    -> AI row missing E or F (we wait for Assistant to fill)
- "Needs Images"     -> AI row has E+F but G empty (we will hit HF)
- "Done"             -> everything filled
- "Couldn't find images" -> Link row search < 3 images
- "Error"            -> transient issues

ENV SECRETS (GitHub Actions -> Repository → Settings → Secrets and variables → Actions):
- GOOGLE_CREDENTIALS_JSON  (service account JSON)
- SHEET_ID                 (the spreadsheet ID)
- WORKSHEET_NAME           (default: Pipeline)
- GOOGLE_API_KEY           (optional; only for Link rows via CSE)
- GOOGLE_CX_ID             (optional; only for Link rows via CSE)
- HF_SPACE_URL             (e.g. https://huggingface.co/spaces/Theloomvale/loomvale-image-lab)
- HF_TOKEN                 (optional; required if Space is private)
- MAX_ROWS_PER_RUN         (optional; default 5)
"""

import os
import re
import time
import json
import hashlib
import requests
import unicodedata
from typing import List, Optional
from urllib.parse import urlparse

import gspread
from google.oauth2.service_account import Credentials

# ---------- Config ----------
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Pipeline")
MAX_ROWS_PER_RUN = int(os.getenv("MAX_ROWS_PER_RUN", "5"))

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX_ID = os.getenv("GOOGLE_CX_ID")

HF_SPACE_URL = os.getenv("HF_SPACE_URL", "").rstrip("/")
HF_TOKEN = os.getenv("HF_TOKEN")

# Sheet headers (new layout)
H_STATUS = "Status"
H_TOPIC = "Topic"
H_SOURCE = "ImageSource"
H_LINKS = "SourceLinks"
H_AMBIENCE = "ImagePrompt_Ambience"
H_SCENES = "ImagePrompt_Scenes"
H_AI_LINKS = "AI generated images"
H_TONE = "Tone"
H_CAP_HASH = "Caption+Hashtags Prompt"
H_ASSIST = "Assistant"

# Link search: allowed domains & extension
PREFERRED_DOMAINS = {
    # official & reputable
    "crunchyroll.com", "ghibli.jp", "aniplex.co.jp", "toho.co.jp", "imdb.com",
    "media-amazon.com", "storyblok.com", "theposterdb.com", "viz.com",
    "myanimelist.net", "netflix.com", "bandainamcoent.co.jp", "fuji.tv",
    "toei-anim.co.jp", "kadokawa.co.jp", "shueisha.co.jp", "avex.com",
    "aniverse-mag.com", "eiga.com", "natalie.mu", "animenewsnetwork.com",
    # allowed fallback
    "pinterest.com", "pinimg.com",
}
EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp)(?:$|\?)", re.IGNORECASE)

# Brand colors (deterministic per topic)
BRAND_COLORS = [
    "Mizu blue", "Soft sage green", "War lantern orange", "Karma beige", "Charcoal gray"
]

# ---------- Google Sheets ----------
def _gc():
    raw = os.environ["GOOGLE_CREDENTIALS_JSON"]
    info = json.loads(raw if raw.strip().startswith("{") else raw)
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds)

def get_ws():
    sh = _gc().open_by_key(os.environ["SHEET_ID"])
    try:
        return sh.worksheet(WORKSHEET_NAME)
    except Exception:
        return sh.sheet1

def header_map(ws) -> dict:
    headers = [h.strip() for h in ws.row_values(1)]
    mapping = {h: i + 1 for i, h in enumerate(headers)}
    # ensure all present (create if needed)
    wanted = [H_STATUS,H_TOPIC,H_SOURCE,H_LINKS,H_AMBIENCE,H_SCENES,H_AI_LINKS,H_TONE,H_CAP_HASH,H_ASSIST]
    updated = False
    for h in wanted:
        if h not in mapping:
            headers.append(h)
            mapping[h] = len(headers)
            updated = True
    if updated:
        ws.update("1:1", [headers])
    return mapping

# ---------- Helpers ----------
def normalize(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()

def color_for_topic(topic: str) -> str:
    idx = int(hashlib.md5(topic.strip().encode()).hexdigest(), 16) % len(BRAND_COLORS)
    return BRAND_COLORS[idx]

def infer_tone(topic: str) -> str:
    t = normalize(topic)
    if any(k in t for k in ["attack on titan","aot","naruto","bleach","jujutsu","demon slayer","one piece","trigun","cyberpunk","blue exorcist","hell's paradise","spy x family"]):
        return "Dramatic, bold with emotional depth"
    if any(k in t for k in ["ghibli","shinkai","your name","weathering with you","princess mononoke","totoro","nausicaa","slice of life","lofi","cozy","desk"]):
        return "Nostalgic, cozy, empathic"
    if any(k in t for k in ["ai","design","ux","ui","ar","vr","3d","tool","hologram","studio"]):
        return "Informative, cozy-tech, empathic"
    if any(k in t for k in ["romance","love","goodbye","letter","memory","heart"]):
        return "Tender, poetic, heartfelt"
    return "Cozy, empathic"

def caption_hashtags_prompt(topic: str, tone: str) -> str:
    # merged, directional-only
    return (
        f"Write an Instagram caption about: {topic}.\n"
        f"Tone: {tone}.\n"
        "Use Loomvale’s cozy, cinematic, empathic voice. Begin with a short emotional hook, then two to three concise sentences, and end with a subtle related call to action. "
        "Maximum 600 characters, no more than two emojis. "
        "Now generate 10 hashtags about the topic; include the # before every word and leave a single space between each hashtag."
    )

def row_is_empty(row: List[str], hdr: dict) -> bool:
    for key in [H_TOPIC,H_SOURCE,H_LINKS,H_AMBIENCE,H_SCENES,H_AI_LINKS,H_TONE,H_CAP_HASH,H_ASSIST]:
        idx = hdr[key]
        val = row[idx-1] if len(row) >= idx else ""
        if str(val).strip():
            return False
    return True

def is_portrait_meta(item: dict, min_h: int = 800) -> bool:
    info = item.get("image") or {}
    try:
        w, h = int(info.get("width", 0)), int(info.get("height", 0))
        return h > w and h >= min_h
    except Exception:
        return False

def is_allowed_link(url: str) -> bool:
    if not EXT_RE.search(url or ""):
        return False
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
        return any(host == d or host.endswith("." + d) for d in PREFERRED_DOMAINS)
    except Exception:
        return False

# ---------- Google CSE (Link rows) ----------
def search_images(topic: str) -> List[str]:
    if not (GOOGLE_API_KEY and GOOGLE_CX_ID):
        return []
    queries = [
        f"{topic} official key visual poster",
        f"{topic} anime key visual portrait",
        f"{topic} poster vertical site:imdb.com OR site:media-amazon.com OR site:crunchyroll.com",
        f"{topic} site:pinterest.com OR site:pinimg.com portrait poster",
    ]
    seen, best = set(), []
    for q in queries:
        try:
            r = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "q": q, "cx": GOOGLE_CX_ID, "key": GOOGLE_API_KEY,
                    "searchType":"image","num":10,"safe":"active"
                }, timeout=20
            )
            data = r.json()
            for it in (data.get("items") or []):
                link = it.get("link")
                if not link or link in seen: 
                    continue
                if is_portrait_meta(it) and is_allowed_link(link):
                    best.append(link)
                    seen.add(link)
                    if len(best) == 3:
                        return best
        except Exception:
            continue
    return best[:3]

# ---------- Hugging Face Space (AI rows) ----------
def call_hf_space(prompt: str, n: int = 5) -> List[str]:
    """
    Calls your Gradio Space /api/predict. Expects it to return images; we then
    read the gallery file names from the JSON response (gradio returns base64 or temp files).
    Your Space already persists images and writes back to the sheet when requested;
    here we call the API in 'URLs only' mode so we can write them ourselves.
    """
    if not HF_SPACE_URL:
        return []
    url = f"{HF_SPACE_URL}/api/predict"
    headers = {"Content-Type":"application/json"}
    if HF_TOKEN:
        headers["Authorization"] = f"Bearer {HF_TOKEN}"

    # These must match your Space's component order. We'll send minimal fields:
    payload = {
        "data": [
            "stabilityai/stable-diffusion-xl-base-1.0",  # model dropdown
            False,                                      # use LCM
            prompt,                                     # prompt textbox
            "text, watermark, signature, logo, jpeg artifacts, lowres, blurry, oversharp, deformed, extra fingers, extra limbs, bad hands, bad anatomy, duplicate, worst quality",  # negative
            1024, 1344, 28, 6.5, n, -1,                 # width, height, steps, cfg, n, seed
            True, True,                                 # save_and_write (ignored), mark_done (ignored)
        ]
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=120)
        r.raise_for_status()
        data = r.json()
        # Gradio returns {"data": [[{"name": "...", "data": "data:image/..."} ...], "status text"]}
        out = data.get("data") or []
        gallery = out[0] if out else []
        urls = []
        for item in gallery:
            # Prefer "origin" URL if Space returns a resolved URL, else skip
            # Many Spaces return temporary file paths; if your Space uploads to repo and returns HTTP links, capture here:
            if isinstance(item, dict):
                if "orig" in item:  # custom key (if you coded it)
                    urls.append(item["orig"])
                elif "name" in item and str(item["name"]).startswith("http"):
                    urls.append(item["name"])
        return urls[:n] if urls else []
    except Exception:
        return []

# ---------- Main processing ----------
def process():
    ws = get_ws()
    hdr = header_map(ws)
    rows = ws.get_all_values()[1:]  # skip header

    updated = 0

    for r_idx, row in enumerate(rows, start=2):
        try:
            if updated >= MAX_ROWS_PER_RUN:
                break

            topic = (row[hdr[H_TOPIC]-1] if len(row) >= hdr[H_TOPIC] else "").strip()
            src_raw = (row[hdr[H_SOURCE]-1] if len(row) >= hdr[H_SOURCE] else "")
            source = src_raw.strip().lower()
            assistant = (row[hdr[H_ASSIST]-1] if len(row) >= hdr[H_ASSIST] else "").strip()
            ai_links = (row[hdr[H_AI_LINKS]-1] if len(row) >= hdr[H_AI_LINKS] else "").strip()

            # Skip if no topic
            if not topic:
                # If the entire row is empty, you could generate new ideas here (optional).
                continue

            tone = infer_tone(topic)
            ws.update_cell(r_idx, hdr[H_TONE], tone)
            ws.update_cell(r_idx, hdr[H_CAP_HASH], caption_hashtags_prompt(topic, tone))

            # ------ LINK ROWS ------
            if source == "link":
                links = search_images(topic)
                if links:
                    ws.update_cell(r_idx, hdr[H_LINKS], ", ".join(links))
                if len(links) >= 3:
                    ws.update_cell(r_idx, hdr[H_ASSIST], "Done")
                else:
                    ws.update_cell(r_idx, hdr[H_ASSIST], "Couldn't find images")
                updated += 1
                time.sleep(0.2)
                continue

            # ------ AI ROWS ------
            if source == "ai":
                ambience = (row[hdr[H_AMBIENCE]-1] if len(row) >= hdr[H_AMBIENCE] else "").strip()
                scenes   = (row[hdr[H_SCENES]-1] if len(row) >= hdr[H_SCENES] else "").strip()

                # 1) If prompts missing -> mark Needs Prompts and skip
                if not ambience or not scenes:
                    ws.update_cell(r_idx, hdr[H_ASSIST], "Needs Prompts")
                    updated += 1
                    time.sleep(0.2)
                    continue

                # 2) If prompts exist but images not generated -> hit HF
                if not ai_links:
                    prompt = (ambience + "\n\n" + scenes).strip()
                    urls = call_hf_space(prompt, n=5)
                    if urls:
                        ws.update_cell(r_idx, hdr[H_AI_LINKS], ", ".join(urls))
                        ws.update_cell(r_idx, hdr[H_ASSIST], "Done")
                    else:
                        ws.update_cell(r_idx, hdr[H_ASSIST], "Needs Images")
                    updated += 1
                    time.sleep(0.2)
                    continue

                # 3) Already has images
                ws.update_cell(r_idx, hdr[H_ASSIST], "Done")
                updated += 1
                time.sleep(0.2)
                continue

            # Unknown source → do nothing but keep tone/caption prompt updated
            updated += 1
            time.sleep(0.1)

        except Exception as e:
            try:
                ws.update_cell(r_idx, hdr[H_ASSIST], f"Error")
            except Exception:
                pass

    print(f"Updated rows: {updated}")

if __name__ == "__main__":
    process()
