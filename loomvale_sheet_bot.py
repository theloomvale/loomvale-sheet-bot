#!/usr/bin/env python3
# Loomvale Sheet Bot — final
# Sheet columns (A→J):
# A Status | B Topic | C ImageSource | D SourceLinks | E ImagePrompt_Ambience |
# F ImagePrompt_Scenes | G AI generated images | H Tone | I Caption+Hashtags Prompt | J Assistant

import os, re, time, json, random, base64, hashlib
from typing import List, Dict, Optional
from urllib.parse import urlparse

import requests
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ------------- Config -------------
MAX_ROWS_PER_RUN = 5          # process at most N rows per run (quality-first)
RETRY_COUNT = 3
PER_QUERY_SLEEP = 0.6
WRITE_SLEEP = 0.10
MIN_PORTRAIT_HEIGHT = 800

# domains (official first) + pinterest as fallback
PREFERRED_DOMAINS = {
    "crunchyroll.com", "ghibli.jp", "aniplex.co.jp", "toho.co.jp", "imdb.com",
    "media-amazon.com", "storyblok.com", "theposterdb.com", "viz.com",
    "myanimelist.net", "netflix.com", "bandainamcoent.co.jp", "fuji.tv",
    "toei-anim.co.jp", "kadokawa.co.jp", "shueisha.co.jp", "avex.com",
    "aniverse-mag.com", "eiga.com", "natalie.mu", "animenewsnetwork.com",
    # allowed fallback (at the end of preference)
    "pinterest.com", "pinimg.com"
}
EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp)(?:$|\?)", re.IGNORECASE)

# deterministic brand colors
BRAND_COLORS = [
    "Mizu blue", "Soft sage green", "War lantern orange", "Karma beige", "Charcoal gray"
]

# ------------- Env / Sheet -------------
SHEET_ID = os.environ["SHEET_ID"]
PIPELINE_TAB = os.environ.get("PIPELINE_TAB", "Pipeline").strip() or None
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
GOOGLE_CX_ID = os.environ.get("GOOGLE_CX_ID", "")

# ------------- Headers (fixed) -------------
H_STATUS   = "Status"                    # A
H_TOPIC    = "Topic"                     # B
H_SOURCE   = "ImageSource"               # C  ("AI" or "Link")
H_LINKS    = "SourceLinks"               # D
H_AMB      = "ImagePrompt_Ambience"      # E
H_SCENES   = "ImagePrompt_Scenes"        # F
H_AI_URLS  = "AI generated images"       # G  (5 URLs, comma-separated; HF fills later)
H_TONE     = "Tone"                      # H
H_CAPHASH  = "Caption+Hashtags Prompt"   # I  (merged)
H_ASSIST   = "Assistant"                 # J  ("Done", "Needs AI Images", "Couldn't find images", "To do", etc.)

# we never modify A/B/C intentionally unless a row is fully empty (idea creation)
NEVER_TOUCH = {H_STATUS, H_TOPIC, H_SOURCE}

# ------------- Utilities -------------
def _load_service_account() -> Credentials:
    """
    Accepts GOOGLE_CREDENTIALS_JSON as:
    - raw JSON string, or
    - base64-encoded JSON, or
    Uses GOOGLE_APPLICATION_CREDENTIALS as path fallback.
    """
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    raw = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
    if raw:
        try:
            if not raw.startswith("{"):
                raw = base64.b64decode(raw).decode("utf-8")
            info = json.loads(raw)
            return Credentials.from_service_account_info(info, scopes=scopes)
        except Exception as e:
            raise RuntimeError(f"Invalid GOOGLE_CREDENTIALS_JSON: {e}")
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not path or not os.path.exists(path):
        raise RuntimeError("Provide GOOGLE_CREDENTIALS_JSON (json or base64) or GOOGLE_APPLICATION_CREDENTIALS path.")
    return Credentials.from_service_account_file(path, scopes=scopes)


def get_ws():
    gc = gspread.authorize(_load_service_account())
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
    required = [H_STATUS, H_TOPIC, H_SOURCE, H_LINKS, H_AMB, H_SCENES, H_AI_URLS, H_TONE, H_CAPHASH, H_ASSIST]
    missing = [h for h in required if h not in mapping]
    if missing:
        raise RuntimeError(f"Missing headers: {missing}\nFound: {headers}")
    return mapping


def row_is_fully_empty(row: List[str], hdr: Dict[str, int]) -> bool:
    # If B..J are all blank → fully empty
    for h in [H_TOPIC, H_SOURCE, H_LINKS, H_AMB, H_SCENES, H_AI_URLS, H_TONE, H_CAPHASH, H_ASSIST]:
        idx = hdr[h]
        val = row[idx-1] if len(row) >= idx else ""
        if str(val).strip():
            return False
    return True


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def infer_tone(topic: str) -> str:
    t = normalize(topic)
    shonen = ["attack on titan","aot","naruto","bleach","jujutsu","demon slayer","one piece","trigun","cyberpunk","blue exorcist","hell's paradise","spy x family"]
    cozy   = ["ghibli","shinkai","totoro","nausicaa","slice of life","lofi","desk","atelier","manga"]
    tech   = ["ai","design","ux","ui","ar","vr","3d","tool","shader","render"]
    rom    = ["romance","love","goodbye","letters","memory","first date","confession"]
    if any(k in t for k in shonen): return "Dramatic, bold with emotional depth"
    if any(k in t for k in cozy):   return "Nostalgic, cozy, empathic"
    if any(k in t for k in tech):   return "Informative, cozy-tech, empathic"
    if any(k in t for k in rom):    return "Tender, poetic, heartfelt"
    return "Cozy, empathic"


def caphash_prompt(topic: str, tone: str) -> str:
    return (
        f"Write an Instagram caption about: {topic}.\n"
        f"Tone: {tone}.\n"
        "Use Loomvale’s cozy, cinematic, empathic voice with a short-story vibe. "
        "Begin with a short emotional hook, then 2–3 concise sentences, end with a subtle related call to action. "
        "Maximum 600 characters, no more than two emojis. "
        "Also generate 10 hashtags related to the topic; include a # before each word and place spaces between hashtags. "
        "Output only the caption text followed by the hashtags on the last line."
    )


def color_for_topic(topic: str) -> str:
    # deterministic color selection by topic hash
    h = int(hashlib.sha256(topic.encode("utf-8")).hexdigest(), 16)
    return BRAND_COLORS[h % len(BRAND_COLORS)]


def infer_archetype(topic: str) -> str:
    t = normalize(topic)
    if any(k in t for k in ["forest","spirit","shrine","wind","mononoke","totoro","kami"]): return "fantasy"
    if any(k in t for k in ["city","neon","rain","train","alley","shibuya","crosswalk"]):   return "urban"
    if any(k in t for k in ["romance","love","goodbye","letter","memory"]):                return "romance"
    if any(k in t for k in ["ai","studio","design","hologram","render","vr","ar"]):        return "tech"
    if any(k in t for k in ["lofi","cat","desk","study","cozy","bedroom","workspace"]):    return "cozy"
    return "cozy"


def character_composition(topic: str) -> str:
    t = normalize(topic)
    if any(k in t for k in ["cat","dog","fox","animal","pet"]):    return "one human and one animal companion"
    if any(k in t for k in ["friends","group","class","team"]):    return "small group of 3–5 friends"
    if any(k in t for k in ["girl","she","her","shoujo"]):         return "two girls with a warm connection"
    if any(k in t for k in ["boy","he","him","shonen"]):           return "one boy protagonist"
    # default duet (popular in your references)
    return "one boy and one girl with gentle chemistry"


def ai_ambience_prompt(topic: str) -> str:
    col = color_for_topic(topic)
    comp = character_composition(topic)
    style_hint = "anime style" if infer_archetype(topic) in ["cozy","fantasy","urban","romance"] else "realistic style"
    return (
        f"(Color Theme: {col})\n"
        f"Overall Style & Tone:\n"
        f"- Lo-fi, painterly look with soft film-grain texture; soft colors and cinematic warmth.\n"
        f"- East Asian character type; {comp}.\n"
        f"- Integrated text look (visual cues only): stylized dialogue bubbles (blank), soft hand-drawn panel captions, faint gray handwritten shapes (not readable).\n"
        f"- Visual language hint: {style_hint}.\n"
    )


def ai_scene_prompt(topic: str) -> str:
    arch = infer_archetype(topic)
    # vary scaffolding per archetype but keep 5-scene structure
    if arch == "cozy":
        scenes = [
            ('“Warm Start”', 'Morning light in a quiet room; a kettle hums; soft desk clutter; a cat yawns.', 'Unhurried comfort.', '(manga font) “Five more minutes.”  (handwritten gray) warm light, slower time'),
            ('“Small Rituals”', 'Hands sketch notes near washi tape, cassette player on.', 'Gentle focus and ordinary magic.', '(manga font) “Almost there.”  (handwritten gray) thoughts line up, softly'),
            ('“Window Pause”', 'Rain beads on glass; city blur; steam from a cup.', 'Reflective calm.', '(manga font) “Listen.”  (handwritten gray) the rain is writing for us'),
            ('“Shared Silence”', 'Two sit close on the floor; lamp glow; cat between them.', 'Quiet togetherness.', '(manga font) “Stay a little.”  (handwritten gray) silence does the talking'),
            ('“Blue Hour”', 'Sky deepens; lights flicker on; room breathes.', 'Soft closure, saved moment.', '(manga font) “Save this.”  (handwritten gray) keep the edges soft'),
        ]
    elif arch == "urban":
        scenes = [
            ('“The Walk Home”', 'Rainy crosswalk; neon reflections; shared umbrella.', 'Shy connection.', '(manga font) “You’ll catch a cold.”  (handwritten gray) he always says it when words fail'),
            ('“Platform Breeze”', 'Train arrives; posters ripple; shoes on wet tiles.', 'Anticipation.', '(manga font) “One stop more.”  (handwritten gray) holding a minute open'),
            ('“City Shelter”', 'Bus stop glass streaked; shared earbud; distant siren.', 'Quiet intimacy.', '(manga font) “Hear that?”  (handwritten gray) our song finds the space'),
            ('“Edge of Goodbye”', 'Doors part; motion blur takes lights apart.', 'Bittersweet.', '(manga font) “See you.”  (handwritten gray) something unsent stays warm'),
            ('“After Rain”', 'Puddles go still; umbrella forgotten on bench.', 'Peaceful acceptance.', '(manga font) “Home, then.”  (handwritten gray) air becomes lighter than before'),
        ]
    elif arch == "fantasy":
        scenes = [
            ('“Forest Breath”', 'Mist between cedars; tiny spirits peeking.', 'Hushed wonder.', '(manga font) “Did you see?”  (handwritten gray) leaves answer softly'),
            ('“Shrine Path”', 'Paper lanterns sway; fox statue watches.', 'Reverent curiosity.', '(manga font) “This way.”  (handwritten gray) old stones remember names'),
            ('“River Mirror”', 'Moon ripples; koi shapes glow faintly.', 'Still magic.', '(manga font) “Listen.”  (handwritten gray) water keeps the stories round'),
            ('“Hidden Gate”', 'Torii in fog; wind paints ribbons.', 'Crossing over.', '(manga font) “Ready?”  (handwritten gray) the door opens like a breath'),
            ('“Returning”', 'Back to moss and dawn birds.', 'Calm return.', '(manga font) “Thank you.”  (handwritten gray) gifts left where light begins'),
        ]
    elif arch == "romance":
        scenes = [
            ('“First Look”', 'Window seat; sun outlining her hair.', 'Fluttering start.', '(manga font) “Hi.”  (handwritten gray) the word is smaller than the feeling'),
            ('“Long Street”', 'Shadows stretch; their steps in time.', 'Growing closeness.', '(manga font) “Don’t rush.”  (handwritten gray) the city walks with us'),
            ('“Hands Almost”', 'Fingers near on a railing; breeze lifts sleeves.', 'Electric pause.', '(manga font) “Almost.”  (handwritten gray) gravity saves the moment'),
            ('“The Letter”', 'Paper creases; stamp pressed; night desk lamp.', 'Tender risk.', '(manga font) “For you.”  (handwritten gray) ink learns the shape of your name'),
            ('“Kept Quiet”', 'Dawn on rooftops; one smile shared.', 'Soft promise.', '(manga font) “See you.”  (handwritten gray) tomorrow opens gently'),
        ]
    else:  # tech / default
        scenes = [
            ('“Studio Boot”', 'Screens glow; UI grids; color swatches.', 'Focused build energy.', '(manga font) “Compile.”  (handwritten gray) ideas align, pixels breathe'),
            ('“Prototype”', 'Hologram ghosting in air; hand gestures.', 'Playful iteration.', '(manga font) “Again.”  (handwritten gray) we trace the future slowly'),
            ('“Model Spin”', '3D turntable; wireframe flicker.', 'Precision calm.', '(manga font) “Clean.”  (handwritten gray) edges soften into story'),
            ('“Render Night”', 'Coffee steam; progress bar crawling.', 'Quiet grind.', '(manga font) “Almost.”  (handwritten gray) the image warms into life'),
            ('“Ship”', 'Post button hovered; sunrise outside.', 'Release ease.', '(manga font) “Go.”  (handwritten gray) let it find its people'),
        ]
    lines = []
    for i, (name, visual, mood, text) in enumerate(scenes, 1):
        lines.append(
            f"Scene {i} – {name}\n"
            f"Visual: {visual}\n"
            f"Mood: {mood}\n"
            f"Text: {text}\n"
        )
    return "\n".join(lines)


def is_allowed_link(url: str) -> bool:
    if not url or not EXT_RE.search(url):
        return False
    try:
        host = urlparse(url).netloc.lower().split(":")[0]
        return any(host == d or host.endswith("." + d) for d in PREFERRED_DOMAINS)
    except Exception:
        return False


def is_portrait(item: dict) -> bool:
    info = item.get("image") or {}
    try:
        w, h = int(info.get("width", 0)), int(info.get("height", 0))
        return (h > w) and (h >= MIN_PORTRAIT_HEIGHT)
    except Exception:
        return False


def search_images(topic: str) -> List[str]:
    if not (GOOGLE_API_KEY and GOOGLE_CX_ID):
        return []
    service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
    queries = [
        f"{topic} official key visual poster",
        f"{topic} key visual portrait",
        f"{topic} anime poster official",
    ]
    seen, best = set(), []
    for q in queries:
        for attempt in range(RETRY_COUNT):
            try:
                resp = service.cse().list(
                    q=q, cx=GOOGLE_CX_ID, searchType="image",
                    num=10, safe="active", imgSize="large"
                ).execute()
                for it in resp.get("items", []) or []:
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
                code = getattr(e, "resp", None).status if getattr(e, "resp", None) else None
                if code in (429, 500, 503):
                    time.sleep(1.2 + attempt)
                    continue
                return best[:3]
            except Exception:
                if attempt == RETRY_COUNT - 1:
                    return best[:3]
                time.sleep(0.8 + attempt)
        time.sleep(PER_QUERY_SLEEP)
    return best[:3]


NERDY_SEEDS = [
    "Lo-fi Cat Nap", "Midnight Code Café", "Pastel Study Desk",
    "Shibuya Alley in Rain", "Forest Shrine Whisper", "Retro Arcade Glow",
    "Dreamcore City Night", "Kintsugi Poster Study", "Cozy Manga Atelier",
    "Train Window Thoughts", "Cyberpunk Hanami", "Rainy Platform Goodbye",
    "Ghibli-inspired Forest Spirits", "Urban Shrine at Blue Hour",
    "Starry Rooftop Studio", "Sunset Vinyl Listening"
]

def generate_nerdy_topics(n: int) -> List[str]:
    random.shuffle(NERDY_SEEDS)
    out = []
    i = 0
    while len(out) < n:
        base = NERDY_SEEDS[i % len(NERDY_SEEDS)]
        # add light variation to avoid duplicates if needed
        if random.random() < 0.4:
            base += f" — {random.choice(['soft rain','blue hour','winter lamp','film-grain','paper lanterns'])}"
        out.append(base)
        i += 1
    return out


def set_cells(ws, row_idx: int, hdr: Dict[str,int], values: Dict[str, str]):
    # write only provided fields
    for h, v in values.items():
        col = hdr[h]
        ws.update_cell(row_idx, col, v)
        time.sleep(WRITE_SLEEP)


def process():
    ws = get_ws()
    hdr = header_map(ws)
    all_rows = ws.get_all_values()[1:]  # data rows (skip header)
    updated = 0

    for r_idx, row in enumerate(all_rows, start=2):
        if updated >= MAX_ROWS_PER_RUN:
            break

        try:
            if row_is_fully_empty(row, hdr):
                # Idea creation in-place
                topic = generate_nerdy_topics(1)[0]
                source = "AI" if random.random() < 0.6 else "Link"
                tone = infer_tone(topic)
                base_values = {
                    H_STATUS: "Ready",
                    H_TOPIC: topic,
                    H_SOURCE: source,
                    H_TONE: tone,
                    H_CAPHASH: caphash_prompt(topic, tone),
                }
                if source.lower() == "ai":
                    base_values.update({
                        H_AMB: ai_ambience_prompt(topic),
                        H_SCENES: ai_scene_prompt(topic),
                        H_ASSIST: "Needs AI Images",  # images to be generated later by HF
                        H_LINKS: "", H_AI_URLS: ""
                    })
                else:
                    links = search_images(topic)
                    base_values.update({
                        H_LINKS: ", ".join(links) if links else "",
                        H_ASSIST: "Done" if len(links) == 3 else "Couldn't find images",
                        H_AMB: "", H_SCENES: "", H_AI_URLS: ""
                    })
                set_cells(ws, r_idx, hdr, base_values)
                updated += 1
                continue

            # Existing row: never change A/B/C unless empty; complete the rest
            topic  = (row[hdr[H_TOPIC]-1]  if len(row) >= hdr[H_TOPIC]  else "").strip()
            source = (row[hdr[H_SOURCE]-1] if len(row) >= hdr[H_SOURCE] else "").strip().lower()
            if not topic or source not in {"ai","link"}:
                continue

            assistant = (row[hdr[H_ASSIST]-1] if len(row) >= hdr[H_ASSIST] else "").strip()
            if assistant == "Done":
                continue

            tone = infer_tone(topic)
            writes = {H_TONE: tone}

            if source == "ai":
                amb   = (row[hdr[H_AMB]-1]    if len(row) >= hdr[H_AMB]    else "").strip()
                scenes= (row[hdr[H_SCENES]-1] if len(row) >= hdr[H_SCENES] else "").strip()
                if not amb:
                    writes[H_AMB] = ai_ambience_prompt(topic)
                if not scenes:
                    writes[H_SCENES] = ai_scene_prompt(topic)
                # caption+hashtags merged prompt always (refresh-safe)
                writes[H_CAPHASH] = caphash_prompt(topic, tone)
                writes[H_ASSIST] = "Needs AI Images"
                set_cells(ws, r_idx, hdr, writes)
                updated += 1
                continue

            if source == "link":
                links = (row[hdr[H_LINKS]-1] if len(row) >= hdr[H_LINKS] else "").strip()
                if not links:
                    found = search_images(topic)
                    if found:
                        writes[H_LINKS]  = ", ".join(found)
                        writes[H_ASSIST] = "Done" if len(found) == 3 else "Couldn't find images"
                    else:
                        writes[H_ASSIST] = "Couldn't find images"
                # caption+hashtags merged prompt always (refresh-safe)
                writes[H_CAPHASH] = caphash_prompt(topic, tone)
                set_cells(ws, r_idx, hdr, writes)
                updated += 1
                continue

        except Exception as e:
            set_cells(ws, r_idx, hdr, {H_ASSIST: f"Error: {str(e)[:80]}"})

    print(f"Finished. Updated {updated} rows (max {MAX_ROWS_PER_RUN}).")


if __name__ == "__main__":
    process()
