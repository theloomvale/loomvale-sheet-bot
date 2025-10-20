import os, io, json, requests, hashlib, time
from urllib.parse import urlparse
from PIL import Image
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

# ----------------- CONFIG -----------------
SHEET_TAB = "Pipeline"  # your sheet tab name

SHEET_ID       = os.getenv("SHEET_ID")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX_ID   = os.getenv("GOOGLE_CX_ID")

# Service account from GitHub Secret (preferred) or local file
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
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!A1:K"
    ).execute()
    return res.get("values", [])

def write_rows_bulk(rows_payload):
    data = []
    for r1, vals in rows_payload:
        if len(vals) < 11:
            vals += [""] * (11 - len(vals))
        rng = f"{SHEET_TAB}!A{r1}:K{r1}"
        data.append({"range": rng, "values": [vals]})
    if not data:
        return
    sheets.values().batchUpdate(
        spreadsheetId=SHEET_ID,
        body={"valueInputOption": "RAW", "data": data}
    ).execute()

# ----------------- IMAGE HELPERS -----------------
def _is_img(url:str)->bool:
    return urlparse(url).path.lower().endswith((".jpg",".jpeg",".png",".webp",".jfif",".pjpeg",".pjp"))

def _bytes(url:str):
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent":"Mozilla/5.0"})
        return r.content if r.status_code == 200 else None
    except Exception:
        return None

def _portrait_and_over_1_4kb(b:bytes)->bool:
    if not b or len(b) < 1400:
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
        "num": min(n,10),
        "safe": "off"
    }
    js = requests.get(url, params=params, timeout=25).json()
    return [i["link"] for i in js.get("items", []) if _is_img(i.get("link",""))]

def find_3_portrait_links(topic:str):
    queries = [
        f"{topic} official key visual OR official poster 4K vertical",
        f"{topic} theatrical poster portrait high resolution",
        f"{topic} promotional art vertical"
    ]
    seen, valid = set(), []
    for q in queries:
        for u in google_images(q, n=10):
            if u in seen:
                continue
            seen.add(u)
            b = _bytes(u)
            if _portrait_and_over_1_4kb(b):
                valid.append(u)
                if len(valid) == 3:
                    return valid
        time.sleep(1.5)  # gentle pause between queries
    return valid

# ----------------- BRAND COLOR LOGIC -----------------
BRAND_THEMES = [
    "Mizu blue",
    "Soft sage green",
    "War lantern orange",
    "Karma beige",
    "Charcoal gray",
]

def theme_for_row(row_index:int, topic:str)->str:
    h = int(hashlib.sha256(f"{row_index}:{topic}".encode("utf-8")).hexdigest(), 16)
    return BRAND_THEMES[h % len(BRAND_THEMES)]

# ----------------- TONE / TEXT -----------------
def tone_for(topic:str):
    t = topic.lower()
    tone_default = "Cozy, empathic"

    if any(k in t for k in ["ghibli","mononoke","nausicaä","nausicaa","shinkai","your name","frieren","magus"]):
        return "Nostalgic, cozy, empathic"
    if any(k in t for k in ["chainsaw man","demon slayer","bleach","attack on titan","trigun","solo leveling","hells paradise","blue exorcist"]):
        return "Dramatic, bold with emotional depth"
    if any(k in t for k in ["ai","tool","design","trend","creative","tech"]):
        return "Informative, cozy-tech, empathic"
    if any(k in t for k in ["romance","love","heart","emotion","connection"]):
        return "Tender, poetic, heartfelt"
    return tone_default

def caption_prompt(topic:str, tone:str):
    return (
        f"Write an Instagram caption about: {topic}. Tone: {tone}, aligned with Loomvale’s cozy-emphatic voice — cinematic warmth, emotional nuance. "
        "Structure: 1 short hook, 2–3 concise lines, subtle CTA (e.g., 'save for later'). Max 300 chars, ≤2 emojis, no hashtags."
    )

def hashtag_prompt(topic:str):
    if any(k in topic.lower() for k in ["ai","tool","design","trend","creative","tech"]):
        return ("Create 20 Instagram hashtag keywords about: {topic}. lowercase tokens, no #, comma-separated, broad+niche.")
    return (f"Create 10 hashtags with '#' included, space-separated, about {topic}. Mix franchise, genre, aesthetic.")

def ai_image_brief(topic:str, primary_theme:str):
    return (
        f"“{topic}” — 5-Image Cinematic Series (Loomvale brand)\n"
        f"Overall Style & Tone:\n"
        f"* Lo-fi, painterly, soft film-grain texture\n"
        f"* Soft colours (cozy, cinematic warmth)\n"
        f"* East Asian character type\n"
        f"* Mixed text style: Manga for dialogue, gray handwritten for narration\n"
        f"* Text integrated naturally within the artwork\n\n"
        f"Brand Color Focus: {primary_theme} — use tasteful variations of {primary_theme}; subtle accents from (Mizu blue, Soft sage green, War lantern orange, Karma beige, Charcoal gray) only if needed.\n"
        f"\nScene 1 — “The Walk Home” (shared umbrella, rain)\nScene 2 — “The Crosswalk” (puddle reflections)\nScene 3 — “The Shelter” (bus stop)\nScene 4 — “The Goodbye” (bus arrival)\nScene 5 — “After the Rain” (quiet bench)\n"
    )

# ----------------- FINALIZE HELPER -----------------
def finalize_ai_links(row_number_1based, topic, ai_links_csv, tone, caption, hashtags, current_status):
    vals = [
        current_status, topic, "AI", "", "", tone, caption, hashtags, "", ai_links_csv, "Done"
    ]
    write_rows_bulk([(row_number_1based, vals)])

# ----------------- MAIN -----------------
def run():
    rows = read_rows()
    if not rows:
        print("No data"); return

    updates = []
    for i, row in enumerate(rows[1:], start=2):
        row += [""] * (11 - len(row))
        cur_status, topic, src = row[0].strip(), row[1].strip(), row[2].strip()
        existing_D, existing_E, existing_F, existing_G, existing_H, existing_J, cur_action = row[3], row[4], row[5], row[6], row[7], row[9], row[10].strip()
        if not topic or cur_action.lower() == "done":
            continue

        tone = existing_F or tone_for(topic)
        cap  = existing_G or caption_prompt(topic, tone)
        tags = existing_H or hashtag_prompt(topic)

        if src.lower() == "ai":
            primary_theme = theme_for_row(i, topic)
            brief = existing_D.strip() if existing_D else ai_image_brief(topic, primary_theme)
            vals = [cur_status, topic, "AI", brief, "", tone, cap, tags, "", existing_J, "Done"]
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
        time.sleep(0.5)

    write_rows_bulk(updates)
    print(f"✅ Updated {len(updates)} row(s).")

if __name__ == "__main__":
    run()
