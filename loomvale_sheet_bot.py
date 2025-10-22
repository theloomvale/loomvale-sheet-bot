#!/usr/bin/env python3
import os
import io
import time
import json
import random
import re
from typing import List, Dict, Optional
from urllib.parse import urlparse

import requests
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# =========================
# ENV / CONSTANTS
# =========================
SHEET_ID = os.environ["SHEET_ID"]
PIPELINE_TAB = os.environ.get("PIPELINE_TAB", "").strip()  # "" => first sheet
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

# Optional (Link rows)
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CX_ID = os.environ.get("GOOGLE_CX_ID", "")

# Optional (AI image generation)
HF_TOKEN = os.environ.get("HF_TOKEN", "")
HF_MODEL = os.environ.get("HF_MODEL", "stabilityai/stable-diffusion-xl-base-1.0")
HF_AUTOGEN = os.environ.get("HF_AUTOGEN", "false").lower() == "true"  # generate now or defer
MAX_ROWS_PER_RUN = int(os.environ.get("MAX_ROWS_PER_RUN", "5"))

# Output image target size (divisible by 8; portrait)
SDXL_W, SDXL_H = 1024, 1344
SDXL_STEPS = 28
SDXL_CFG = 6.5
NEGATIVE_PROMPT = (
    "text, watermark, signature, logo, jpeg artifacts, lowres, blurry, oversharp, "
    "deformed, extra fingers, extra limbs, bad hands, bad anatomy, duplicate, worst quality"
)

# Headers (exactly as in the sheet)
H_STATUS = "Status"
H_TOPIC = "Topic"
H_SOURCE = "ImageSource"
H_LINKS = "SourceLinks"
H_AMBIENCE = "ImagePrompt_Ambience"
H_SCENES = "ImagePrompt_Scenes"
H_AI_URLS = "AI generated images"
H_TONE = "Tone"
H_CAPTAG = "Caption+Hashtag Prompt"
H_ASSIST = "Assistant"

# Allowed write columns
ALLOWED_WRITE = {H_LINKS, H_AMBIENCE, H_SCENES, H_TONE, H_CAPTAG, H_AI_URLS, H_ASSIST}

# Preferred image domains
PREFERRED_DOMAINS = {
    "crunchyroll.com", "ghibli.jp", "aniplex.co.jp", "toho.co.jp", "imdb.com",
    "media-amazon.com", "storyblok.com", "theposterdb.com", "viz.com",
    "myanimelist.net", "netflix.com", "bandainamcoent.co.jp", "fuji.tv",
    "toei-anim.co.jp", "kadokawa.co.jp", "shueisha.co.jp", "avex.com", "aniverse-mag.com",
    "eiga.com", "natalie.mu", "natalie.mu/comic", "animenewsnetwork.com",
    # allowed fallback:
    "pinterest.com", "pinimg.com"
}
IMG_EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp)(?:$|\?)", re.IGNORECASE)

BRAND_COLORS = ["Mizu blue", "Soft sage green", "War lantern orange", "Karma beige", "Charcoal gray"]


# =========================
# GOOGLE HELPERS
# =========================
def _load_sa_creds():
    raw = GOOGLE_CREDENTIALS_JSON
    if not raw.startswith("{"):
        raw = json.loads(raw)  # allow base64 step elsewhere if you ever add it
        return Credentials.from_service_account_info(raw, scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ])
    info = json.loads(GOOGLE_CREDENTIALS_JSON)
    return Credentials.from_service_account_info(info, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])


def get_ws():
    creds = _load_sa_creds()
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    if PIPELINE_TAB:
        try:
            return sh.worksheet(PIPELINE_TAB)
        except Exception:
            pass
    return sh.sheet1


def header_map(ws) -> Dict[str, int]:
    headers = [h.strip() for h in ws.row_values(1)]
    mapping = {h: i + 1 for i, h in enumerate(headers)}
    for name in [H_STATUS, H_TOPIC, H_SOURCE, H_LINKS, H_AMBIENCE, H_SCENES, H_AI_URLS, H_TONE, H_CAPTAG, H_ASSIST]:
        if name not in mapping:
            raise RuntimeError(f"Missing header '{name}'. Found: {headers}")
    return mapping


def drive_service():
    creds = _load_sa_creds()
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_image_to_drive(image_bytes: bytes, name: str) -> str:
    """Uploads bytes to Drive and returns a shareable link."""
    svc = drive_service()
    file_metadata = {"name": name, "mimeType": "image/png"}
    media = MediaIoBaseUpload(io.BytesIO(image_bytes), mimetype="image/png", resumable=False)
    f = svc.files().create(body=file_metadata, media_body=media, fields="id, webViewLink, webContentLink").execute()
    file_id = f["id"]
    # make public read
    svc.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader"}).execute()
    # Prefer webContentLink for direct download; webView is preview
    return f.get("webContentLink") or f.get("webViewLink")


# =========================
# IMAGE SEARCH (Link rows)
# =========================
def _host_allowed(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
        if any(host == d or host.endswith("." + d) for d in PREFERRED_DOMAINS):
            return True
    except Exception:
        return False
    return False


def _portrait(image_obj: dict) -> bool:
    im = image_obj.get("image") or {}
    try:
        w, h = int(im.get("width", 0)), int(im.get("height", 0))
        return h > w and h >= 800
    except Exception:
        return False


def search_poster_links(topic: str, max_results=3) -> List[str]:
    if not (GOOGLE_API_KEY and GOOGLE_CX_ID):
        return []
    queries = [
        f"{topic} official key visual poster",
        f"{topic} key visual portrait",
        f"{topic} anime poster official",
    ]
    out, seen = [], set()
    for q in queries:
        try:
            r = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={
                    "q": q, "cx": GOOGLE_CX_ID, "key": GOOGLE_API_KEY,
                    "searchType": "image", "num": 10, "safe": "active"
                },
                timeout=20
            )
            items = (r.json() or {}).get("items", []) or []
            for it in items:
                link = it.get("link")
                if not link or link in seen:
                    continue
                if not IMG_EXT_RE.search(link):
                    continue
                if not _host_allowed(link):
                    continue
                if not _portrait(it):
                    continue
                out.append(link)
                seen.add(link)
                if len(out) >= max_results:
                    return out
        except Exception:
            continue
    return out


# =========================
# AI PROMPTING HELPERS
# =========================
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def archetype(topic: str) -> str:
    t = _norm(topic)
    if any(w in t for w in ["cat", "lofi", "desk", "atelier", "study", "cozy", "room", "nap"]):
        return "cozy"
    if any(w in t for w in ["forest", "shrine", "spirit", "wind", "myth", "dragon"]):
        return "fantasy"
    if any(w in t for w in ["city", "neon", "rain", "train", "subway", "night"]):
        return "urban"
    if any(w in t for w in ["love", "goodbye", "letter", "memory", "heart", "romance"]):
        return "romance"
    if any(w in t for w in ["ai", "design", "ux", "studio", "hologram", "creative", "tech"]):
        return "tech"
    return "cozy"


def infer_tone(topic: str) -> str:
    arc = archetype(topic)
    if arc == "cozy":
        return "Cozy, empathic"
    if arc == "fantasy":
        return "Nostalgic, cozy, empathic"
    if arc == "urban":
        return "Dramatic, bold with emotional depth"
    if arc == "romance":
        return "Tender, poetic, heartfelt"
    if arc == "tech":
        return "Informative, cozy-tech, empathic"
    return "Cozy, empathic"


def deterministic_color(topic: str) -> str:
    idx = abs(hash(_norm(topic))) % len(BRAND_COLORS)
    return BRAND_COLORS[idx]


def ambience_prompt(topic: str) -> str:
    color = deterministic_color(topic)
    arc = archetype(topic)
    style_hint = "anime" if arc in ("cozy", "fantasy", "urban", "romance") else "realistic"
    return (
        f"(Color Theme: {color})\n"
        "Overall Style & Tone: lo-fi, painterly, soft film-grain texture; soft colours; East Asian character type; "
        "mixed text style with stylized dialogue bubbles (blank), soft hand-drawn panel captions, "
        "faint gray handwritten shapes (not readable); text integrated naturally into the artwork. "
        f"Primary rendering hint: {style_hint}."
    )


def scene_block(topic: str) -> str:
    arc = archetype(topic)
    if arc == "cozy":
        scenes = [
            ("Morning window", "Warm sun on desk; steam from mug; cat tail flicks in frame",
             'Girl (manga font): "five more minutes." / (handwritten gray) warm light, slower time.'),
            ("Bus ride", "Rain on glass; headphones; city blur outside",
             '(handwritten gray) a song you only love on rainy days.'),
            ("Notebook", "Close-up of pencil notes; stickers; coffee stains",
             '(handwritten gray) the page forgives my messy heart.'),
            ("Alley bakery", "Paper bag; illustrated buns; soft neon reflections",
             'Boy (manga font): "still warm."'),
            ("Quiet night", "Cassette player; soft lamp; curtains breathe",
             '(handwritten gray) midnight, not lonely—just softer.')
        ]
    elif arc == "urban":
        scenes = [
            ("Crosswalk", "Neon reflections; umbrellas; motion blur",
             'Girl (manga font): "don’t rush."'),
            ("Train window", "City grids; foggy glass; small heart sticker",
             '(handwritten gray) memories ride backwards.'),
            ("Rooftop", "Distant siren; skyline glow; jacket flutter",
             'Boy (manga font): "breathe."'),
            ("Arcade", "CRT glow; coins; claw machine plush",
             '(handwritten gray) losing is part of the charm.'),
            ("Street ramen", "Steam cloud; plastic stools; laughter haze",
             '(handwritten gray) salt & comfort.')
        ]
    elif arc == "fantasy":
        scenes = [
            ("Forest edge", "Morning mist; tiny spirits in moss",
             '(handwritten gray) the quiet knows my name.'),
            ("Shrine", "Paper talismans sway; fox mask half-lit",
             'Girl (manga font): "stay a little."'),
            ("Riverbank", "Lanterns drift; ripples echo stars",
             '(handwritten gray) wishes float easier than words.'),
            ("Wind hill", "Tall grass; ribbon in breeze; distant bells",
             'Boy (manga font): "listen."'),
            ("Night gate", "Torii silhouette; fireflies script the air",
             '(handwritten gray) the path remembers.')
        ]
    elif arc == "romance":
        scenes = [
            ("Walk home", "Shared umbrella; hands almost touch",
             'Boy (manga font): "you’ll catch a cold." / (handwritten gray) saying what he can.'),
            ("Crosswalk", "Neon puddles; quiet mist",
             'Girl (manga font): "the rain’s softer now."'),
            ("Bus shelter", "Shared earbud; raindrops on glass",
             '(handwritten gray) the song ends before we do.'),
            ("Goodbye", "Door slides; motion blur; his eyes down",
             'Boy (manga font): "see you."'),
            ("After rain", "Forgotten umbrella; golden hush",
             '(handwritten gray) even the air is quieter.')
        ]
    else:  # tech
        scenes = [
            ("Studio", "Monitors glow; graph paper; sticky notes",
             '(handwritten gray) draft → iterate → wonder.'),
            ("Light table", "Tracing film; markers; gentle shadow",
             'Girl (manga font): "again."'),
            ("Prototype", "3D-printed curve; hands align parts",
             '(handwritten gray) precise is a feeling.'),
            ("Presentation", "Projector dust; murmurs; cursor blink",
             'Boy (manga font): "ship it."'),
            ("Night shift", "LED strips; tea tin; rubber duck debugger",
             '(handwritten gray) solved by moonlight.')
        ]
    # Format 5 scenes
    lines = []
    for i, (title, visual, text) in enumerate(scenes, 1):
        lines.append(
            f"Scene {i} – “{title}”\n"
            f"Visual: {visual}.\n"
            f"Mood: cinematic intimacy.\n"
            f"Text: {text}"
        )
    return "\n\n".join(lines)


def caption_hashtag_prompt(topic: str, tone: str) -> str:
    return (
        f"Write an Instagram caption about: {topic}. Tone: {tone}. "
        "Use Loomvale’s cozy, cinematic, empathic voice. Begin with a short emotional hook, "
        "then two to three concise sentences, and end with a subtle CTA like “save for later.” "
        "Maximum 600 characters, no more than two emojis. "
        "Also generate 10 hashtags, each with # and space-separated, topical & aesthetic."
    )


# =========================
# HUGGING FACE GENERATION
# =========================
def sdxl_single(prompt: str, seed: Optional[int] = None) -> Optional[bytes]:
    if not HF_TOKEN:
        return None
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    payload = {
        "inputs": prompt,
        "parameters": {
            "negative_prompt": NEGATIVE_PROMPT,
            "height": SDXL_H,
            "width": SDXL_W,
            "guidance_scale": SDXL_CFG,
            "num_inference_steps": SDXL_STEPS,
            "seed": seed if seed is not None else random.randint(1, 2_000_000_000),
        }
    }
    r = requests.post(
        f"https://api-inference.huggingface.co/models/{HF_MODEL}",
        headers=headers, json=payload, timeout=120
    )
    if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
        return r.content
    return None


def generate_n_images_to_drive(prompt: str, topic: str, n: int = 5) -> List[str]:
    urls = []
    for i in range(n):
        img = sdxl_single(prompt)
        if not img:
            continue
        link = upload_image_to_drive(img, name=f"{topic[:40]}_{int(time.time())}_{i+1}.png")
        urls.append(link)
    return urls


# =========================
# IDEA SEEDS
# =========================
SEED_TOPICS = [
    "Midnight Code Cafe", "Pastel Workspace Flatlay", "Lofi Cat Nap",
    "Rain City Crosswalk", "Shibuya Night Train", "Notebook Therapy Desk",
    "Forest Shrine at Dawn", "Pocket Walkman Vibes", "Ghibli-esque Tea Break",
    "Arcade Neon Memory"
]


def new_topic() -> str:
    return random.choice(SEED_TOPICS)


def choose_source_for_topic(topic: str) -> str:
    # 60% AI, 40% Link
    return "AI" if random.random() < 0.6 else "Link"


# =========================
# MAIN PROCESS
# =========================
def process():
    ws = get_ws()
    hdr = header_map(ws)
    rows = ws.get_all_values()[1:]  # skip header

    updated = 0

    for r_idx, row in enumerate(rows, start=2):
        try:
            status = (row[hdr[H_STATUS] - 1] if len(row) >= hdr[H_STATUS] else "").strip()
            topic = (row[hdr[H_TOPIC] - 1] if len(row) >= hdr[H_TOPIC] else "").strip()
            source_raw = (row[hdr[H_SOURCE] - 1] if len(row) >= hdr[H_SOURCE] else "")
            source = source_raw.strip().lower()
            assistant = (row[hdr[H_ASSIST] - 1] if len(row) >= hdr[H_ASSIST] else "").strip()

            # If everything is empty → create a full idea
            fully_empty = all(not (row[hdr[c]-1].strip() if len(row) >= hdr[c] else "") for c in
                              [H_TOPIC, H_SOURCE, H_LINKS, H_AMBIENCE, H_SCENES, H_AI_URLS, H_TONE, H_CAPTAG, H_ASSIST])

            if fully_empty or status == "" or status.lower() == "ready":
                if not topic:
                    topic = new_topic()
                    ws.update_cell(r_idx, hdr[H_TOPIC], topic)
                if not source:
                    source = choose_source_for_topic(topic).lower()
                    ws.update_cell(r_idx, hdr[H_SOURCE], source.capitalize())
                if not status:
                    ws.update_cell(r_idx, hdr[H_STATUS], "Ready")

            # Do not re-touch Done rows
            if assistant.lower() == "done":
                continue

            if source == "link":
                # Fill links; leave E/F/G empty
                links = search_poster_links(topic)
                if links:
                    ws.update_cell(r_idx, hdr[H_LINKS], ", ".join(links))
                    ws.update_cell(r_idx, hdr[H_TONE], infer_tone(topic))
                    ws.update_cell(r_idx, hdr[H_CAPTAG], caption_hashtag_prompt(topic, infer_tone(topic)))
                    ws.update_cell(r_idx, hdr[H_ASSIST], "Done")
                else:
                    ws.update_cell(r_idx, hdr[H_ASSIST], "Couldn't find images")

                updated += 1

            elif source == "ai":
                # Always write ambience/scenes/tone/caption prompt
                amb = ambience_prompt(topic)
                scn = scene_block(topic)
                tone = infer_tone(topic)

                ws.update_cell(r_idx, hdr[H_AMBIENCE], amb)
                ws.update_cell(r_idx, hdr[H_SCENES], scn)
                ws.update_cell(r_idx, hdr[H_TONE], tone)
                ws.update_cell(r_idx, hdr[H_CAPTAG], caption_hashtag_prompt(topic, tone))

                if HF_AUTOGEN:
                    # Build one combined prompt for SDXL (ambience + the five scenes)
                    prompt = f"{amb}\n\n{scn}"
                    urls = generate_n_images_to_drive(prompt, topic, n=5)
                    if urls:
                        ws.update_cell(r_idx, hdr[H_AI_URLS], ", ".join(urls))
                        ws.update_cell(r_idx, hdr[H_ASSIST], "Done")
                    else:
                        ws.update_cell(r_idx, hdr[H_ASSIST], "Generate Images")
                else:
                    ws.update_cell(r_idx, hdr[H_ASSIST], "Generate Images")

                updated += 1

            else:
                # unknown source: skip
                pass

            if updated >= MAX_ROWS_PER_RUN:
                print(f"Reached MAX_ROWS_PER_RUN={MAX_ROWS_PER_RUN}")
                break

            time.sleep(0.25)

        except Exception as e:
            print(f"Row {r_idx} error: {e}")

    print(f"Done. Updated: {updated}")


# ================
# HF FOLLOW-UP WORKER
# ================
def generate_pending_images():
    """Run by the 30-min workflow: picks rows with Assistant='Generate Images' and fills G with new URLs."""
    if not HF_TOKEN:
        print("HF_TOKEN not set; skipping.")
        return

    ws = get_ws()
    hdr = header_map(ws)
    rows = ws.get_all_values()[1:]  # skip header

    done = 0
    for r_idx, row in enumerate(rows, start=2):
        try:
            assistant = (row[hdr[H_ASSIST]-1].strip() if len(row) >= hdr[H_ASSIST] else "")
            source = (row[hdr[H_SOURCE]-1].strip().lower() if len(row) >= hdr[H_SOURCE] else "")
            topic = (row[hdr[H_TOPIC]-1].strip() if len(row) >= hdr[H_TOPIC] else "")
            if assistant != "Generate Images" or source != "ai" or not topic:
                continue

            amb = (row[hdr[H_AMBIENCE]-1] if len(row) >= hdr[H_AMBIENCE] else "")
            scn = (row[hdr[H_SCENES]-1] if len(row) >= hdr[H_SCENES] else "")
            if not amb or not scn:
                # nothing to generate from
                ws.update_cell(r_idx, hdr[H_ASSIST], "Needs prompts")
                continue

            prompt = f"{amb}\n\n{scn}"
            urls = generate_n_images_to_drive(prompt, topic, n=5)
            if urls:
                ws.update_cell(r_idx, hdr[H_AI_URLS], ", ".join(urls))
                ws.update_cell(r_idx, hdr[H_ASSIST], "Done")
                done += 1
            else:
                ws.update_cell(r_idx, hdr[H_ASSIST], "HF failed")

            time.sleep(0.25)

            if done >= MAX_ROWS_PER_RUN:
                break

        except Exception as e:
            print(f"Pending gen row {r_idx} error: {e}")

    print(f"Image generation filled: {done}")


if __name__ == "__main__":
    mode = os.environ.get("MODE", "process")
    if mode == "generate":
        generate_pending_images()
    else:
        process()
