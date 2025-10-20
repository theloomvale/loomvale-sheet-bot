# Loomvale Sheet Bot — Link/AI rows + auto-topic seeding (brand version)
# - Never edits Column A (Status)
# - Processes only rows where K != "Done"
# - LINK rows: finds 3 ultra-hi-res PORTRAIT poster/key-visual DIRECT URLs → E, writes long cinematic caption brief → G, hashtags brief → H, sets K
# - AI rows: writes Loomvale 5-image brief with ONE brand color focus → D, plus G/H, preserves J, sets K
# - Empty Topic rows: discovers fresh Loomvale-flavored topics and APPENDS new rows (A=Ready, C=Link, K="To do")

import os, io, json, requests, hashlib, time, random
from urllib.parse import urlparse
from PIL import Image
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

# ----------------- REQUIRED ENV VARS (GitHub Secrets) -----------------
# SHEET_ID                 -> your Google Sheet ID
# GOOGLE_CREDENTIALS_JSON  -> paste full JSON of your Service Account
# GOOGLE_API_KEY           -> Google API key (for Custom Search)
# GOOGLE_CX_ID             -> Programmable Search Engine ID

# ----------------- CONFIG -----------------
SHEET_TAB = "Pipeline"          # sheet tab name; headers A..K must match spec
MIN_BYTES = 1400                # 1.4 KB minimum
USER_AGENT = "Mozilla/5.0 (Loomvale Sheet Bot)"

# Loomvale brand palette (ONE color focus per AI prompt)
BRAND_THEMES = [
    "Mizu blue",
    "Soft sage green",
    "War lantern orange",
    "Karma beige",
    "Charcoal gray",
]

# Discovery queries tuned to Loomvale (anime, K-culture, design, travel aesthetics)
DISCOVERY_QUERIES = [
    "site:imdb.com anime film poster 2025",
    "site:crunchyroll.com news key visual",
    "site:aniplexusa.com key visual",
    "site:ghibli.jp works poster",
    "site:kimetsu.com visual",
    "anime season 2 teaser poster key visual",
    "k-culture design exhibition poster 2025",
    "studio trigger mecha key visual",
]

# ----------------- AUTH -----------------
SHEET_ID       = os.getenv("SHEET_ID")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX_ID   = os.getenv("GOOGLE_CX_ID")

if os.getenv("GOOGLE_CREDENTIALS_JSON"):
    creds = Credentials.from_service_account_info(
        json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON")),
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
else:
    creds = Credentials.from_service_account_file(
        "credentials.json",
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )

sheets = build("sheets", "v4", credentials=creds).spreadsheets()

# ----------------- SHEETS HELPERS -----------------
def read_rows():
    res = sheets.values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1:K"
    ).execute()
    return res.get("values", [])

def write_rows_bulk(rows_payload):
    if not rows_payload:
        return
    data = []
    for r1, vals in rows_payload:
        if len(vals) < 11:
            vals += [""] * (11 - len(vals))
        data.append({"range": f"{SHEET_TAB}!A{r1}:K{r1}", "values": [vals]})
    sheets.values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"valueInputOption": "RAW", "data": data}
    ).execute()

def append_rows(rows_to_append):
    if not rows_to_append:
        return
    sheets.values().append(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!A:K",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows_to_append}
    ).execute()

# ----------------- IMAGE HELPERS -----------------
def _is_img(url:str)->bool:
    return urlparse(url).path.lower().endswith((".jpg",".jpeg",".png",".webp",".jfif",".pjpeg",".pjp"))

def _fetch_bytes(url:str):
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent": USER_AGENT})
        return r.content if r.status_code == 200 else None
    except Exception:
        return None

def _portrait_and_over_min(b:bytes)->bool:
    if not b or len(b) < MIN_BYTES:
        return False
    try:
        w, h = Image.open(io.BytesIO(b)).size
        return h > w
    except Exception:
        return False

def google_images(query, n=10):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CX_ID,
        "q": query,
        "searchType": "image",
        "num": min(n, 10),
        "safe": "off"
    }
    js = requests.get(url, params=params, timeout=25).json()
    items = js.get("items", []) or []
    return [i.get("link") for i in items if _is_img(i.get("link",""))]

def find_3_portrait_links(topic:str):
    queries = [
        f"{topic} official poster OR key visual 4K vertical",
        f"{topic} portrait poster high resolution",
        f"{topic} promotional art vertical poster",
    ]
    seen, valid = set(), []
    for q in queries:
        for u in google_images(q, n=10):
            if not u or u in seen:
                continue
            seen.add(u)
            b = _fetch_bytes(u)
            if _portrait_and_over_min(b):
                valid.append(u)
                if len(valid) == 3:
                    return valid
        time.sleep(1.0)
    return valid

# ----------------- TOPIC DISCOVERY (Loomvale flavor) -----------------
def google_web_titles(query, n=5):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CX_ID,
        "q": query,
        "num": min(n, 10),
        "safe": "off"
    }
    js = requests.get(url, params=params, timeout=25).json()
    items = js.get("items", []) or []
    titles = []
    for it in items:
        t = (it.get("title") or "").strip()
        if t:
            titles.append(t)
    return titles

def normalize_topic_from_title(title:str)->str:
    cut = title.split(" - ")[0].split(" — ")[0].strip()
    return cut[:120]

def discover_new_topics(limit=6):
    pool = set()
    random.shuffle(DISCOVERY_QUERIES)
    for q in DISCOVERY_QUERIES[:6]:
        for t in google_web_titles(q, n=6):
            norm = normalize_topic_from_title(t)
            if len(norm) > 5:
                pool.add(norm)
        time.sleep(0.8)
        if len(pool) >= limit:
            break
    return list(pool)[:limit]

# ----------------- TEXT / TONE (Loomvale) -----------------
def theme_for_row(row_index:int, topic:str)->str:
    h = int(hashlib.sha256(f"{row_index}:{topic}".encode("utf-8")).hexdigest(), 16)
    return BRAND_THEMES[h % len(BRAND_THEMES)]

def tone_for(topic:str):
    """Cozy + empathic baseline; category-aware."""
    t = topic.lower()
    if any(k in t for k in ["ghibli","mononoke","nausicaä","nausicaa","shinkai","your name","frieren","magus"]):
        return "Nostalgic, cozy, empathic"
    if any(k in t for k in ["chainsaw man","demon slayer","bleach","attack on titan","trigun","solo leveling","hells paradise","blue exorcist","jujutsu"]):
        return "Dramatic, bold with emotional depth"
    if any(k in t for k in ["ai","tool","design","trend","creative","tech","innovation"]):
        return "Informative, cozy-tech, empathic"
    if any(k in t for k in ["romance","love","heart","emotion","connection"]):
        return "Tender, poetic, heartfelt"
    return "Cozy, empathic"

def caption_prompt(topic:str, tone:str):
    # Long cinematic caption brief
    return (
        f"Write a cinematic Instagram caption about: {topic}. Tone: {tone}, Loomvale’s cozy-empathic voice. "
        "Start with a short emotional hook, then 2–3 concise lines (world, craft, or story stakes), "
        "end with a subtle CTA (e.g., 'save for later'). Max ~300 chars, up to 2 emojis, no hashtags."
    )

def hashtag_prompt(topic:str):
    if any(k in topic.lower() for k in ["ai","tool","design","trend","creative","tech"]):
        return "Create 20 instagram hashtag keywords about the topic; lowercase tokens, no #, comma-separated, broad+niche."
    return f"Create 10 hashtags with # included, space-separated, about {topic}. Mix franchise, genre, aesthetic."

def ai_image_brief(topic:str, primary_theme:str):
    return (
        f"“{topic}” — 5-Image Cinematic Series (Loomvale brand)\n"
        f"Overall Style & Tone:\n"
        f"* Lo-fi, painterly, soft film-grain texture\n"
        f"* Soft colours (cozy, cinematic warmth)\n"
        f"* East Asian character type\n"
        f"* Mixed text style: Manga dialogue + gray handwritten narration\n"
        f"* Text integrated naturally within the artwork\n\n"
        f"Brand Color Focus: {primary_theme} — use tasteful variations of {primary_theme}; "
        f"subtle accents from (Mizu blue, Soft sage green, War lantern orange, Karma beige, Charcoal gray) only if needed.\n\n"
        f"1) Walk Home — shared umbrella, gentle rain  \n"
        f"2) Crosswalk — puddle reflections, quiet city glow  \n"
        f"3) Shelter — bus stop glass, rain streaks, shared earbuds  \n"
        f"4) Goodbye — bus arriving, soft motion blur  \n"
        f"5) After the Rain — intimate close, soft gradients\n"
    )

# ----------------- MAIN -----------------
def run():
    """
    Sheet headers (row 1 EXACT):
    A Status | B Topic | C ImageSource | D ImagePrompt | E SourceLinks | F Tone | G CaptionPrompt | H HashtagPrompt | I (unused) | J AI Image Links | K To be actioned

    Rules:
    - Never change A (Status).
    - Only edit rows where K != "Done".
    - LINK rows: write E (0–3 direct portrait URLs), G long cinematic caption brief, H hashtags.
        * If 3 URLs → K="Done"
        * If 1–2 URLs → K="Needs Images"
        * If 0 URLs → K stays or becomes "Needs Images"
    - AI rows: write D brief (ONE brand color focus), G caption brief, H hashtags; preserve J; K="Done".
    - Empty Topic rows: discover new topics and APPEND new rows (A=Ready, C=Link, K="To do").
    """
    rows = read_rows()
    if not rows:
        print("No data")
        return

    updates = []
    found_empty_topic_row = False

    for i, row in enumerate(rows[1:], start=2):
        row += [""] * (11 - len(row))  # pad to A..K
        cur_status = row[0].strip()
        topic      = row[1].strip()
        src        = row[2].strip()
        existing_D = row[3]
        existing_E = row[4]
        existing_F = row[5]
        existing_G = row[6]
        existing_H = row[7]
        existing_J = row[9]
        cur_action = row[10].strip()

        # Track empty topic rows for later seeding
        if not topic:
            found_empty_topic_row = True
            continue

        # Skip already actioned rows
        if cur_action.lower() == "done":
            continue

        # Derive text
        tone = existing_F or tone_for(topic)
        cap  = existing_G or caption_prompt(topic, tone)
        tags = existing_H or hashtag_prompt(topic)

        if src.lower() == "ai":
            primary_theme = theme_for_row(i, topic)
            brief = (existing_D or "").strip() or ai_image_brief(topic, primary_theme)
            vals = [cur_status, topic, "AI", brief, "", tone, cap, tags, "", existing_J, "Done"]
            updates.append((i, vals))

        else:
            urls = find_3_portrait_links(topic)
            links_csv = ", ".join(urls) if urls else existing_E
            if len(urls) >= 3:
                k_val = "Done"
            elif len(urls) > 0:
                k_val = "Needs Images"
            else:
                k_val = cur_action or "Needs Images"
            vals = [cur_status, topic, "Link", "", links_csv, tone, cap, tags, "", "", k_val]
            updates.append((i, vals))

        time.sleep(0.4)

    if updates:
        write_rows_bulk(updates)
        print(f"✅ Updated {len(updates)} row(s).")

    if found_empty_topic_row:
        topics = discover_new_topics(limit=6)
        append_payload = []
        for t in topics:
            t_tone = tone_for(t)
            t_cap  = caption_prompt(t, t_tone)
            t_hash = hashtag_prompt(t)
            append_payload.append([
                "Ready",  # A
                t,        # B Topic
                "Link",   # C
                "", "",   # D,E
                t_tone,   # F
                t_cap,    # G
                t_hash,   # H
                "", "",   # I,J
                "To do"   # K
            ])
        if append_payload:
            append_rows(append_payload)
            print(f"🆕 Seeded {len(append_payload)} new topic row(s).")

if __name__ == "__main__":
    run()
