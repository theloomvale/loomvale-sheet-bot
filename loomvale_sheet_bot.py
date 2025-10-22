#!/usr/bin/env python3
"""
Loomvale Sheet Bot (final)
- Sheet columns (A->I):
  A Status | B Topic | C ImageSource | D SourceLinks | E ImagePrompt_Ambience | F ImagePrompt_Scenes | G Tone | H Caption+Hashtags Prompt | I Assistant
- Processes up to 5 rows per run.
- Link rows: finds up to 3 portrait (h>w, h>=800) URLs from trusted domains (official first, then reputable, Pinterest allowed). If <3 -> Assistant="Couldn't find images" (will retry next runs).
- AI rows: writes Ambience+Scenes (Hugging Face readable), Tone, merged Caption+Hashtags prompt; Assistant="Done".
- Empty rows (B..I blank): filled in-place with nerdy ideas; chooses AI vs Link by topic heuristic; fills all fields.
"""
import base64, json, os, re, time, unicodedata, hashlib, random
from typing import List, Optional
from urllib.parse import urlparse

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ===== ENV =====
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON","").strip()
SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
GOOGLE_CX_ID = os.environ["GOOGLE_CX_ID"]
WORKSHEET_NAME: Optional[str] = os.environ.get("WORKSHEET_NAME","Pipeline")

# ===== HEADERS (A->I) =====
H_STATUS,H_TOPIC,H_SOURCE,H_LINKS,H_AMBIENCE,H_SCENES,H_TONE,H_CAP_HASHTAG,H_ASSIST = (
  "Status","Topic","ImageSource","SourceLinks","ImagePrompt_Ambience","ImagePrompt_Scenes","Tone","Caption+Hashtags Prompt","Assistant"
)

# ===== BEHAVIOR =====
MAX_ROWS_PER_RUN = 5
PER_QUERY_SLEEP = 0.6
WRITE_SLEEP = 0.1
RETRY_COUNT = 3

# ===== DOMAINS / FILTERS =====
TRUSTED = {
  "crunchyroll.com","ghibli.jp","aniplex.co.jp","toho.co.jp","imdb.com","media-amazon.com","storyblok.com",
  "theposterdb.com","posterdb.com","viz.com","myanimelist.net","netflix.com","bandainamcoent.co.jp","fuji.tv",
  "toei-animation.com","kadokawa.co.jp","shueisha.co.jp","avex.com","bandaivisual.co.jp","aniverse-mag.com",
  "eiga.com","natalie.mu","animenewsnetwork.com","pinterest.com","pinimg.com"
}
EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp)(?:$|\?)", re.IGNORECASE)

# ===== CREDS / SHEET =====
def _load_service_account_creds():
    scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    raw = GOOGLE_CREDENTIALS_JSON
    if not raw: raise RuntimeError("GOOGLE_CREDENTIALS_JSON is required (raw JSON or base64).")
    if not raw.startswith("{"):
        try: raw = base64.b64decode(raw).decode("utf-8")
        except Exception: pass
    info = json.loads(raw)
    return Credentials.from_service_account_info(info, scopes=scopes)

def get_ws():
    gc = gspread.authorize(_load_service_account_creds())
    sh = gc.open_by_key(SHEET_ID)
    name = (WORKSHEET_NAME or "").strip()
    if name:
        try: return sh.worksheet(name)
        except Exception: pass
    return sh.sheet1

def header_map(ws)->dict:
    headers=[h.strip() for h in ws.row_values(1)]
    mapping={h:i+1 for i,h in enumerate(headers)}
    for n in [H_STATUS,H_TOPIC,H_SOURCE,H_LINKS,H_AMBIENCE,H_SCENES,H_TONE,H_CAP_HASHTAG,H_ASSIST]:
        if n not in mapping: raise RuntimeError(f"Missing header '{n}'. Found: {headers}")
    return mapping

# ===== TEXT / TONE =====
def normalize(s:str)->str:
    return "".join(c for c in unicodedata.normalize("NFKD",s) if not unicodedata.combining(c)).lower()

def infer_tone(topic:str)->str:
    t=normalize(topic)
    if any(k in t for k in ["attack on titan","aot","naruto","bleach","jujutsu","demon slayer","one piece","trigun","cyberpunk","blue exorcist","hell's paradise","spy x family"]):
        return "Dramatic, bold with emotional depth"
    if any(k in t for k in ["ghibli","shinkai","your name","weathering with you","princess mononoke","totoro","nausicaa","slice of life","lofi","cozy"]):
        return "Nostalgic, cozy, empathic"
    if any(k in t for k in ["ai","design","ux","ui","ar","vr","3d","tooling","midjourney","runway","hologram","tech"]):
        return "Informative, cozy-tech, empathic"
    if any(k in t for k in ["romance","love","kiss","goodbye","letter","memory","home","heart"]):
        return "Tender, poetic, heartfelt"
    return "Cozy, empathic"

def brand_color(topic:str)->str:
    colors = ["Mizu blue","Soft sage green","War lantern orange","Karma beige","Charcoal gray"]
    idx = int(hashlib.sha256((topic or '').encode("utf-8")).hexdigest(), 16) % len(colors)
    return colors[idx]

def style_hint(topic:str)->str:
    t=normalize(topic)
    if any(k in t for k in ["photo","realistic","cinema","film still","portrait photo"]): return "realistic"
    return "anime"

# ===== CAPTION + HASHTAGS (merged, directions-only) =====
def merged_caption_hashtags_prompt(topic:str, tone:str)->str:
    return (f"Write an Instagram caption about: {topic}. Tone: {tone}. "
            "Cinematic, cozy, empathic voice—like a short visual story. Max 600 characters, ≤2 emojis. End with a gentle CTA. "
            "Then write 10 hashtags with #, space-separated, about the topic (no explanation).")

# ===== AI PROMPT builders (Ambience & Scenes) =====
def ambience_prompt(topic:str)->str:
    color = brand_color(topic)
    style = style_hint(topic)
    return (
        f"Color theme: {color}. Aesthetic: lo-fi, painterly, soft film-grain texture; cinematic warmth; soft colors; "
        "East Asian character type. Integrated text look (visual only styling cues): stylized dialogue bubbles (blank), "
        "soft hand-drawn panel captions, faint gray handwritten shapes (some readable quotes allowed). "
        f"Primary style hint: {style}. Keep framing natural and human, gentle light, cozy intimacy."
    )

def scenes_prompt(topic:str)->str:
    cw = brand_color(topic).split()[-1].lower()
    def line(s): return s.replace("“", "\"").replace("”","\"").replace("’","'")
    scenes = [
        ("Scene 1 – Soft Start",
         f"Visual: cozy desk or setting related to {topic}; {cw} accents in light and props. "
         "Mood: quiet, inviting. "
         "Text: (manga font): \"five more minutes.\" (handwritten gray) warm light, slower time."),
        ("Scene 2 – Ambient Motion",
         f"Visual: curtains or foliage move; steam from cup; {cw} tint feathers across surfaces. "
         "Mood: calm routine. "
         "Text: (handwritten gray) the day hums softly."),
        ("Scene 3 – Intimate Focus",
         f"Visual: hands, pen, keys, or a small companion (cat/dog); {cw} rim light. "
         "Mood: tender concentration. "
         "Text: (manga font): \"stay a little.\" (handwritten gray) the quiet says more."),
        ("Scene 4 – Pause",
         f"Visual: a yawn; unfinished sketch; window light shifts toward {cw}. "
         "Mood: stillness. "
         "Text: (handwritten gray) here is the middle of the moment."),
        ("Scene 5 – Afterglow",
         f"Visual: room rests; companion asleep; {cw} afterlight on the floor. "
         "Mood: contented closure. "
         "Text: (handwritten gray) keep this small softness.")
    ]
    return "\n\n".join(f"{t}\n{line(desc)}" for t,desc in scenes)

# ===== IMAGE SEARCH =====
def is_portrait(item:dict)->bool:
    info=item.get("image") or {}
    try:
        w,h=int(info.get("width",0)),int(info.get("height",0)); return h>w and h>=800
    except Exception: return False

def allowed_link(url:str)->bool:
    if not EXT_RE.search(url or ""): return False
    try:
        host=urlparse(url).netloc.lower().split(":")[0]
        return any(host==d or host.endswith("."+d) for d in TRUSTED)
    except Exception: return False

def search_images(topic:str)->List[str]:
    service = build("customsearch","v1",developerKey=GOOGLE_API_KEY)
    queries = [
        f'{topic} anime official key visual poster',
        f'{topic} key visual vertical poster',
        f'{topic} movie poster key visual'
    ]
    seen,best=set(),[]
    for q in queries:
        for attempt in range(RETRY_COUNT):
            try:
                resp = service.cse().list(
                    q=q, cx=GOOGLE_CX_ID,
                    searchType="image", num=10, safe="active",
                    imgType="photo", imgSize="xlarge", dateRestrict="d60"
                ).execute()
                items = resp.get("items",[]) or []
                for it in items:
                    link=it.get("link")
                    if not link or link in seen: continue
                    if is_portrait(it) and allowed_link(link):
                        best.append(link); seen.add(link)
                        if len(best)>=3: return best[:3]
                break
            except HttpError as e:
                if getattr(e,"resp",None) and e.resp.status in (429,500,503):
                    time.sleep(1.0+attempt); continue
                raise
            except Exception:
                if attempt==RETRY_COUNT-1: raise
                time.sleep(0.7+attempt)
        time.sleep(PER_QUERY_SLEEP)
    return best[:3]

# ===== IDEAS =====
IDEAS = [
 "Lofi Cat Nap","Neon Rain Crosswalk","Cozy Manga Atelier","Shrine under Dawn Mist",
 "Retro Arcade Memory","Shibuya Night Window Ride","Ghibli-Style Forest Spirits",
 "Pastel Workspace Flatlay","Urban Shrine Blue Hour","Cyberpunk Hanami",
 "Anime Study Desk with Cassette","Autumn Walk with Headphones","Dreamcore City Night",
 "Kintsugi Poster Concept","Creative Moodboard Wall"
]

def choose_imagesource_for_topic(topic:str)->str:
    t=normalize(topic)
    if any(k in t for k in ["lofi","desk","nap","studio","cat","dog","cozy","romance","ghibli","shinkai","fantasy","spirit","shrine","city","neon","rain","train","ai","design","tech"]):
        return "AI"
    return "Link"

# ===== ROW HELPERS =====
def row_is_fully_empty(row:list, hdr:dict)->bool:
    # B..I empty
    for h in [H_TOPIC,H_SOURCE,H_LINKS,H_AMBIENCE,H_SCENES,H_TONE,H_CAP_HASHTAG,H_ASSIST]:
        idx = hdr[h]; val = row[idx-1] if len(row)>=idx else ""
        if str(val).strip(): return False
    return True

def batch_update(ws, updates):
    if not updates: return
    ws.batch_update([{"range": u["range"], "values": [u["values"]]} for u in updates])

# ===== MAIN =====
def process():
    ws=get_ws(); hdr=header_map(ws); data=ws.get_all_values()
    rows=data[1:]  # skip header
    updated=0

    for r_idx, row in enumerate(rows, start=2):
        try:
            if updated>=MAX_ROWS_PER_RUN:
                print(f"Reached MAX_ROWS_PER_RUN={MAX_ROWS_PER_RUN}")
                break

            assistant = (row[hdr[H_ASSIST]-1] if len(row)>=hdr[H_ASSIST] else "").strip()
            if assistant=="Done":
                continue

            # Empty row? Fill in-place
            if row_is_fully_empty(row, hdr):
                topic = random.choice(IDEAS)
                source = choose_imagesource_for_topic(topic)
                tone = infer_tone(topic)
                updates=[]

                if source=="AI":
                    amb = ambience_prompt(topic)
                    scn = scenes_prompt(topic)
                    cap = merged_caption_hashtags_prompt(topic, tone)
                    updates += [
                        {"range": f"A{r_idx}", "values": ["Ready"]},
                        {"range": f"B{r_idx}", "values": [topic]},
                        {"range": f"C{r_idx}", "values": [source]},
                        {"range": f"E{r_idx}", "values": [amb]},
                        {"range": f"F{r_idx}", "values": [scn]},
                        {"range": f"G{r_idx}", "values": [tone]},
                        {"range": f"H{r_idx}", "values": [cap]},
                        {"range": f"I{r_idx}", "values": ["To do"]},
                    ]
                else:
                    links = search_images(topic)
                    cap = merged_caption_hashtags_prompt(topic, tone)
                    status = "Done" if len(links)==3 else "Couldn't find images"
                    updates += [
                        {"range": f"A{r_idx}", "values": ["Ready"]},
                        {"range": f"B{r_idx}", "values": [topic]},
                        {"range": f"C{r_idx}", "values": [source]},
                        {"range": f"D{r_idx}", "values": [", ".join(links)]},
                        {"range": f"G{r_idx}", "values": [tone]},
                        {"range": f"H{r_idx}", "values": [cap]},
                        {"range": f"I{r_idx}", "values": [status]},
                    ]
                batch_update(ws, updates); updated+=1; time.sleep(WRITE_SLEEP); continue

            # If Status+Topic exist, fill rest based on Topic
            topic = (row[hdr[H_TOPIC]-1] if len(row)>=hdr[H_TOPIC] else "").strip()
            source_raw = (row[hdr[H_SOURCE]-1] if len(row)>=hdr[H_SOURCE] else "")
            source = source_raw.strip().lower()
            if not topic: continue

            tone = infer_tone(topic); updates=[]

            if source=="link":
                links = search_images(topic)
                cap = merged_caption_hashtags_prompt(topic, tone)
                status = "Done" if len(links)==3 else "Couldn't find images"
                if links: updates.append({"range": f"D{r_idx}", "values": [", ".join(links)]})
                updates += [
                    {"range": f"G{r_idx}", "values": [tone]},
                    {"range": f"H{r_idx}", "values": [cap]},
                    {"range": f"I{r_idx}", "values": [status]},
                ]
                batch_update(ws, updates); updated+=1; time.sleep(WRITE_SLEEP)

            elif source=="ai":
                amb = ambience_prompt(topic)
                scn = scenes_prompt(topic)
                cap = merged_caption_hashtags_prompt(topic, tone)
                updates += [
                    {"range": f"E{r_idx}", "values": [amb]},
                    {"range": f"F{r_idx}", "values": [scn]},
                    {"range": f"G{r_idx}", "values": [tone]},
                    {"range": f"H{r_idx}", "values": [cap]},
                    {"range": f"I{r_idx}", "values": ["Done"]},
                ]
                batch_update(ws, updates); updated+=1; time.sleep(WRITE_SLEEP)

        except Exception as e:
            print(f"Error on row {r_idx}: {e}")
            try: ws.update_cell(r_idx, hdr[H_ASSIST], "Error")
            except: pass

    print(f"Done. Updated rows: {updated}")

if __name__=="__main__":
    process()
