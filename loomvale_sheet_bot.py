# Loomvale Sheet Bot — Link/AI rows + auto-topic seeding (brand version)
# Non-destructive:
#  - Never edits Column A (Status)
#  - Processes only rows where K != "Done"
#  - LINK rows: finds up to 3 ultra-hi-res PORTRAIT direct poster/key-visual URLs → E
#               writes long cinematic caption brief → G, hashtag brief → H, sets K
#  - AI rows: writes Loomvale 5-image brief with ONE brand color focus → D,
#             writes G/H, preserves J, sets K
#  - Empty Topic rows anywhere: discovers new topics and APPENDS rows (A=Ready, C=Link, K="To do")

import os, io, json, requests, hashlib, time, random
from urllib.parse import urlparse
from PIL import Image
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

# ----------------- REQUIRED SECRETS/ENVS -----------------
# SHEET_ID                 -> your Google Sheet ID
# GOOGLE_CREDENTIALS_JSON  -> full JSON of your Service Account (paste into secret)
# GOOGLE_API_KEY           -> Google API key (Custom Search API enabled)
# GOOGLE_CX_ID             -> Programmable Search Engine ID

# ----------------- SHEET CONFIG -----------------
SHEET_TAB  = "Pipeline"  # tab name
MIN_BYTES  = 1400        # >= 1.4 KB
USER_AGENT = "Mozilla/5.0 (Loomvale Sheet Bot)"

# Loomvale brand palette (ONE color focus per AI prompt)
BRAND_THEMES = [
    "Mizu blue",
    "Soft sage green",
    "War lantern orange",
    "Karma beige",
    "Charcoal gray",
]

# Discovery queries tuned to Loomvale world
DISCOVERY_QUERIES = [
    "site:imdb.com anime film poster 2025",
    "site:crunchyroll.com news key visual",
    "site:aniplexusa.com key visual",
    "site:ghibli.jp works poster",
    "site:kimetsu.com visual",
    "anime season 2 teaser poster key visual",
    "studio trigger mecha key visual",
]

# ------------- AUTH -------------
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

# ------------- SHEETS HELPERS -------------
def read_rows():
    res = sheets.values().get(
        spreadsheetId=SHEET_ID, range=f"{SHEET_TAB}!A1:K"
    ).execute()
    return res.get("values", [])

def write_rows_bulk(rows_payload):
    """rows_payload: list of (row_index_1based, [A..K])"""
    if not rows_payload: return
    data = []
    for r1, vals in rows_payload:
        if len(vals) < 11: vals += [""] * (11 - len(vals))
        data.append({"range": f"{SHEET_TAB}!A{r1}:K{r1}", "values": [vals]})
    sheets.values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"valueInputOption": "RAW", "data": data}
    ).execute()

def append_rows(rows_to_append):
    if not rows_to_append: return
    sheets.values().append(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!A:K",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows_to_append}
    ).execute()

# ------------- IMAGE HELPERS (IMPROVED) -------------
def _is_img(url:str)->bool:
    path = urlparse(url).path.lower()
    return path.endswith((".jpg",".jpeg",".png",".webp",".jfif",".pjpeg",".pjp"))

# trusted poster/key visual hosts (add more as you encounter them)
TRUSTED_POSTER_DOMAINS = {
    "m.media-amazon.com", "images-na.ssl-images-amazon.com",
    "impawards.com", "www.impawards.com",
    "aniplexusa.com", "www.aniplexusa.com",
    "kimetsu.com", "www.kimetsu.com",
    "ghibli.jp", "www.ghibli.jp",
    "crunchyroll.com", "www.crunchyroll.com",
    "toho.co.jp", "www.toho.co.jp",
    "trigun-anime.com", "spyroom-anime.com",
    "sololeveling-anime.net",
    "madeinabyss.jp", "bst-anime.com",
    "haikyu.jp", "ichigoproduction.com"
}

def google_images(query, n=10):
    """CSE image search, prefer largest available images."""
    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_API_KEY,
        "cx": GOOGLE_CX_ID,
        "q": query,
        "searchType": "image",
        "num": min(n, 10),
        "safe": "off",
        "imgSize": "xxlarge"
    }
    js = requests.get(url, params=params, timeout=25).json()
    items = js.get("items", []) or []
    links = []
    for it in items:
        link = (it.get("link") or "").strip()
        mime = (it.get("mime") or "").lower()
        if not link: 
            continue
        if mime.startswith("image/") or _is_img(link):
            links.append(link)
    return links

def _fetch_bytes(url:str, referer: str | None = None):
    """Try GET; if blocked, HEAD for Content-Length; return bytes (or dummy bytes of same length)."""
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    try:
        r = requests.get(url, timeout=25, headers=headers, stream=True)
        if r.status_code == 200:
            return r.content
        # Fallback: HEAD
        hr = requests.head(url, timeout=15, headers=headers, allow_redirects=True)
        if hr.status_code == 200:
            try:
                clen = int(hr.headers.get("Content-Length", "0"))
                return b"." * clen if clen > 0 else None
            except Exception:
                return None
    except Exception:
        return None

def find_3_portrait_links(topic:str):
    """
    Multi-pass:
      1) Search xxlarge images.
      2) If bytes available → verify portrait + >= MIN_BYTES.
      3) If blocked but trusted + good extension → accept.
    """
    queries = [
        f'{topic} official poster OR "key visual" vertical 4K',
        f"{topic} portrait poster high resolution",
        f"{topic} promotional art vertical poster",
    ]
    seen, valid = set(), []
    for q in queries:
        for u in google_images(q, n=10):
            if not u or u in seen: 
                continue
            seen.add(u)

            host = urlparse(u).hostname or ""
            on_trusted = host in TRUSTED_POSTER_DOMAINS

            b = _fetch_bytes(u, referer=f"https://{host}") if host else _fetch_bytes(u)
            if b and len(b) >= MIN_BYTES:
                try:
                    w, h = Image.open(io.BytesIO(b)).size
                    if h > w:
                        valid.append(u)
                        if len(valid) == 3: 
                            return valid
                    else:
                        continue
                except Exception:
                    # If decode fails but domain+ext are trustworthy, accept
                    if on_trusted and _is_img(u):
                        valid.append(u)
                        if len(valid) == 3:
                            return valid
            else:
                if on_trusted and _is_img(u):
                    valid.append(u)
                    if len(valid) == 3:
                        return valid
        time.sleep(0.8)
    return valid

# ------------- TOPIC DISCOVERY -------------
def google_web_titles(query, n=5):
    url = "https://www.googleapis.com/customsearch/v1"
    params = {"key": GOOGLE_API_KEY, "cx": GOOGLE_CX_ID, "q": query, "num": min(n, 10), "safe": "off"}
    js = requests.get(url, params=params, timeout=25).json()
    items = js.get("items", []) or []
    titles = []
    for it in items:
        t = (it.get("title") or "").strip()
        if t: titles.append(t)
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
        if len(pool) >= limit: break
    return list(pool)[:limit]

# ------------- TEXT / TONE (Loomvale) -------------
def theme_for_row(row_index:int, topic:str)->str:
    h = int(hashlib.sha256(f"{row_index}:{topic}".encode("utf-8")).hexdigest(), 16)
    return BRAND_THEMES[h % len(BRAND_THEMES)]

def tone_for(topic:str):
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

# ------------- MAIN -------------
def run():
    """
    Expected headers (row 1 EXACT order):
    A Status | B Topic | C ImageSource | D ImagePrompt | E SourceLinks | F Tone | G CaptionPrompt | H HashtagPrompt | I FinalImage | J AI Image Links | K Assistant

    Rules:
    - Never change A (Status).
    - Only edit rows where K != "Done".
    - LINK rows: fill E (0–3 direct portrait URLs), G caption brief, H hashtag brief.
        * 3 URLs → K="Done"
        * 1–2 URLs → K="Needs Images"
        * 0 URLs → K stays/→ "Needs Images"
    - AI rows: write D brief (ONE brand color focus), G caption brief, H hashtags; keep J; K="Done".
    - Empty Topic rows anywhere → discover topics and APPEND (A=Ready, C=Link, K="To do").
    """
    rows = read_rows()
    if not rows:
        print("No data"); return

    updates = []
    found_empty_topic_row = False

    for i, row in enumerate(rows[1:], start=2):
        row += [""] * (11 - len(row))  # pad to 11 cols
        cur_status = row[0].strip()   # A
        topic      = row[1].strip()   # B
        src        = row[2].strip()   # C
        existing_D = row[3]           # D
        existing_E = row[4]           # E
        existing_F = row[5]           # F
        existing_G = row[6]           # G
        existing_H = row[7]           # H
        existing_J = row[9]           # J
        cur_action = row[10].strip()  # K (Assistant / To be actioned)

        # If Topic empty, we’ll seed later
        if not topic:
            found_empty_topic_row = True
            continue

        # Skip already completed
        if cur_action.lower() == "done":
            continue

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

        time.sleep(0.4)  # polite pacing

    # Write updates
    if updates:
        write_rows_bulk(updates)
        print(f"✅ Updated {len(updates)} row(s).")

    # If any blank Topic rows existed, append fresh topics
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
