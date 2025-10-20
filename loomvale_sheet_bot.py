# top of file
import time
import os, io, json, requests
from urllib.parse import urlparse
from PIL import Image
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials

# ---- env ----
SHEET_ID       = os.getenv("SHEET_ID")                      # your Google Sheet ID
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")                # Custom Search JSON API key
GOOGLE_CX_ID   = os.getenv("GOOGLE_CX_ID")                  # Programmable Search cx

# service account (from GitHub Secret or local file)
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

# ---- sheets helpers ----
SHEET_TAB = "Pipeline"  # your Google Sheet tab name

def read_rows():
    res = sheets.values().get(
        spreadsheetId=SHEET_ID,
        range=f"{SHEET_TAB}!A1:H"
    ).execute()
    return res.get("values", [])

def write_row(r1, values):
    rng = f"{SHEET_TAB}!A{r1}:H{r1}"
    sheets.values().update(
        spreadsheetId=SHEET_ID,
        range=rng,
        valueInputOption="RAW",
        body={"values": [values]}
    ).execute()
    time.sleep(1.2)  # <= keep under 60 writes/min
    
# ---- image helpers ----
def _is_img(url:str)->bool:
    return urlparse(url).path.lower().endswith((".jpg",".jpeg",".png",".webp",".jfif",".pjpeg",".pjp"))

def _bytes(url:str):
    try:
        r = requests.get(url, timeout=25, headers={"User-Agent":"Mozilla/5.0"})
        return r.content if r.status_code == 200 else None
    except Exception:
        return None

def _portrait_and_over_1_4kb(b:bytes)->bool:
    if not b or len(b) < 1400:  # >1.4 KB (your minimum)
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
    # tuned for portrait, poster/key visual, hi-res
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
    return valid

# ---- text helpers ----
def tone_for(topic:str):
    t = topic.lower()
    if any(k in t for k in ["chainsaw man","demon slayer","bleach","attack on titan","trigun","solo leveling","hells paradise"]):
        return "Dramatic, bold"
    if any(k in t for k in ["ghibli","mononoke","nausicaä","nausicaa","shinkai","your name"]):
        return "Nostalgic, cinematic"
    if any(k in t for k in ["ai","tool","design","trend","creative","tech"]):
        return "Informative, creative"
    return "Cinematic, reflective"

def caption_prompt(topic:str, tone:str):
    return (f"Write an Instagram caption about: {topic}. Tone: {tone}. "
            "Hook + 2–3 short lines + subtle CTA. Max 300 chars. ≤2 emojis. No hashtags.")

def hashtag_prompt(topic:str):
    if any(k in topic.lower() for k in ["ai","tool","design","trend","creative","tech"]):
        return ("Create 20 Instagram hashtag keywords about: {topic}. "
                "lowercase tokens, no #, no spaces, comma-separated, broad+niche.")
    return (f"Create 10 hashtags with '#' included, space-separated, about {topic}. "
            "Mix franchise, genre, aesthetic.")

def ai_image_brief(topic:str):
    return (f"“{topic}” — 5-Image Cinematic Series\n"
            "• lo-fi painterly film grain; pastel greens + muted gold; gentle rain; cozy cinematic light\n"
            "Typography: manga dialogue; gray handwritten narration; text integrated\n"
            "1) Walk Home (shared umbrella) — “You’ll catch a cold.”\n"
            "2) Crosswalk (puddle reflections) — narration: wish the rain stayed\n"
            "3) Shelter (bus stop, shared earbuds) — narration: the song was ending\n"
            "4) Goodbye (bus arrives) — “See you.”\n"
            "5) After the Rain (quiet close) — narration: the air feels quieter\n")

# ---- main ----
def run():
    rows = read_rows()
    if not rows:
        print("No data"); return

    # Expect columns: A Status, B Topic, C ImageSource, D ImagePrompt, E SourceLinks, F Tone, G CaptionPrompt, H HashtagPrompt
    for i, row in enumerate(rows[1:], start=2):  # skip header; 1-based indexing for Sheets
        row += [""] * (8 - len(row))
        status, topic, src = row[0].strip(), row[1].strip(), row[2].strip()

        if not topic or status.lower() == "completed" or status.lower() != "ready":
            continue

        t    = tone_for(topic)
        cap  = caption_prompt(topic, t)
        tags = hashtag_prompt(topic)

        if src.lower() == "ai":
            prompt = ai_image_brief(topic)
            write_row(i, ["Completed", topic, "AI", prompt, "", t, cap, tags])
            print(f"Row {i}: AI prompt ✓")
        else:
            urls  = find_3_portrait_links(topic)
            links = ", ".join(urls)
            write_row(i, ["Completed", topic, "Link", "", links, t, cap, tags])
            print(f"Row {i}: {len(urls)} image links ✓")

if __name__ == "__main__":
    # install once locally if needed:
    # pip install google-api-python-client google-auth google-auth-oauthlib requests pillow
def run():
    rows = read_rows()
    if not rows:
        print("No data"); 
        return

    for i, row in enumerate(rows[1:], start=2):  # header on row 1
        row += [""] * (8 - len(row))  # A..H
        cur_status, topic, src = row[0].strip(), row[1].strip(), row[2].strip()

        if not topic or cur_status.lower() != "ready":
            continue  # only process explicit Ready rows

        tone = tone_for(topic)
        cap  = caption_prompt(topic, tone)
        tags = hashtag_prompt(topic)

        if src.lower() == "ai":
            # create brief, DO NOT mark Completed; keep Status unchanged
            brief = ai_image_brief(topic).strip()
            write_row(i, [cur_status, topic, "AI", brief, "", tone, cap, tags])
            print(f"Row {i}: AI prompt written (status kept: {cur_status})")

        else:
            # treat as Link (deep search)
            urls = find_3_portrait_links(topic)
            links = ", ".join(urls)
            if len(urls) >= 3:
                # only now mark Completed
                write_row(i, ["Completed", topic, "Link", "", links, tone, cap, tags])
                print(f"Row {i}: 3 links found → Completed")
            elif len(urls) > 0:
                # partial: keep status, save what we found
                write_row(i, [cur_status, topic, "Link", "", links, tone, cap, tags])
                print(f"Row {i}: {len(urls)} link(s) found (status kept: {cur_status})")
            else:
                # nothing found: refresh text fields, leave links empty & status unchanged
                write_row(i, [cur_status, topic, "Link", "", "", tone, cap, tags])
                print(f"Row {i}: no links found (status kept: {cur_status})")
