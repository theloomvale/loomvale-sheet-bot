#!/usr/bin/env python3
import base64, json, os, re, time, unicodedata, hashlib
from typing import List, Optional
from urllib.parse import urlparse

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON","").strip()
SHEET_ID = os.environ["SHEET_ID"]
GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
GOOGLE_CX_ID = os.environ["GOOGLE_CX_ID"]
WORKSHEET_NAME: Optional[str] = os.environ.get("WORKSHEET_NAME","Pipeline")

H_STATUS,H_TOPIC,H_SOURCE,H_PROMPT,H_LINKS,H_TONE,H_CAPTION,H_HASHTAG,H_FINAL,H_AI_LINKS,H_ASSIST=(
"Status","Topic","ImageSource","ImagePrompt","SourceLinks","Tone","CaptionPrompt","HashtagPrompt","FinalImage","AI Image Links","Assistant")

MAX_ROWS_PER_RUN = 5
PER_QUERY_SLEEP = 0.6
WRITE_SLEEP = 0.12
RETRY_COUNT = 3

PREFERRED_DOMAINS = {"crunchyroll.com","ghibli.jp","aniplex.co.jp","toho.co.jp","imdb.com",
"theposterdb.com","posterdb.com","toei.co.jp","viz.com","animatetimes.com",
"mantan-web.jp","dengekionline.com","kadokawa.co.jp","fuji.tv",
"bandainamcoent.co.jp","netflix.com","horrorsociety.com",
"media-amazon.com","storyblok.com","myanimelist.net"}
BAD_DOMAINS = {"pinterest.","fandom.","reddit.","twitter.","x.com"}
EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp)(?:$|\?)", re.IGNORECASE)

def _load_service_account_creds():
    scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    if not GOOGLE_CREDENTIALS_JSON:
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON is required (raw JSON or base64).")
    raw = GOOGLE_CREDENTIALS_JSON
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
    needed=[H_STATUS,H_TOPIC,H_SOURCE,H_PROMPT,H_LINKS,H_TONE,H_CAPTION,H_HASHTAG,H_FINAL,H_AI_LINKS,H_ASSIST]
    for n in needed:
        if n not in mapping: raise RuntimeError(f"Missing header '{n}'. Found: {headers}")
    return mapping

def normalize(s:str)->str:
    return "".join(c for c in unicodedata.normalize("NFKD",s) if not unicodedata.combining(c)).lower()

def infer_tone(topic:str)->str:
    t=normalize(topic)
    if any(k in t for k in ["attack on titan","aot","naruto","bleach","jujutsu","demon slayer","one piece","trigun","cyberpunk","blue exorcist","hell's paradise","spy x family"]): return "Dramatic, bold with emotional depth"
    if any(k in t for k in ["ghibli","shinkai","your name","weathering with you","princess mononoke","totoro","nausicaa","slice of life","beastars"]): return "Nostalgic, cozy, empathic"
    if any(k in t for k in ["ai","design","ux","ui","ar","vr","3d","tooling","midjourney","runway"]): return "Informative, cozy-tech, empathic"
    if any(k in t for k in ["romance","love","feelings","kimi ni todoke","horimiya","fruits basket"]): return "Tender, poetic, heartfelt"
    return "Cozy, empathic"

def caption_prompt(topic:str,tone:str)->str:
    return (f"Write an Instagram caption about: {topic}.\n"
            f"Tone: {tone}.\n"
            "Loomvale’s cozy-empathic voice, cinematic warmth, emotional nuance.\n"
            "Start with a short emotional hook, then 2–3 concise lines, end with a subtle CTA (\"save for later\").\n"
            "Max 300 chars, ≤2 emojis, no hashtags.")

def hashtag_prompt(topic:str)->str:
    return ("20 lowercase keyword tags (comma-separated, no #). Focus on tech/ai/design terms and aesthetics."
            if any(k in normalize(topic) for k in ["ai","design","ux","ui","ar","vr","3d","tool"]) else
            "10 hashtags with # included, space-separated, mixing franchise, genre, and aesthetic.")


def select_brand_color(topic: str) -> str:
    colors = ["Mizu blue", "Soft sage green", "War lantern orange", "Karma beige", "Charcoal gray"]
    if not topic:
        return colors[0]
    idx = int(hashlib.sha256(topic.encode("utf-8")).hexdigest(), 16) % len(colors)
    return colors[idx]

def ai_image_prompt(topic: str) -> str:
    color = select_brand_color(topic)
    color_noun = {
        "Mizu blue": "blue",
        "Soft sage green": "green",
        "War lantern orange": "orange",
        "Karma beige": "beige",
        "Charcoal gray": "gray"
    }.get(color, "blue")

    header = (
        f"AI IMAGE PROMPT (Color Theme: {color})\n"
        "Overall Style & Tone:\n"
        "* Lo-fi, painterly, soft film-grain texture\n"
        "* Soft colours\n"
        "* East Asian character type\n"
        "* Mixed text style:\n"
        "    * Manga font for spoken dialogue\n"
        "    * Gray handwritten font for inner narration\n"
        "* Text integrated naturally within the artwork\n\n"
    )

    scenes = f"""
Scene 1 – “The Walk Home”
Visual: A soft, rainy afternoon. The boy and girl walk side by side under a shared {color_noun} umbrella. Their hands almost touch, but don’t. Rain glows faintly against the city lights behind them.
Mood: Quiet, shy connection.
Text:
Boy (manga font): “You’ll catch a cold.”
(handwritten gray) He always said it when he didn’t know what else to say.

Scene 2 – “The Crosswalk”
Visual: A wide side angle. The boy and girl stand at a crosswalk, reflections of neon {color_noun} lights flickering in puddles. The rain has softened into mist.
Mood: Stillness and unspoken longing.
Text:
Girl (manga font): “The rain’s softer now.”
(handwritten gray) I wish it would never stop.

Scene 3 – “The Shelter”
Visual: Inside a small bus stop shelter. Rain streaks down glass panels. The two sit side by side, one shared earbud connecting them. A faint {color_noun} glow from a nearby streetlight falls across their faces.
Mood: Gentle intimacy, the calm before separation.
Text:
(handwritten gray) The song was ending.
(handwritten gray) So was something else.

Scene 4 – “The Goodbye”
Visual: A bus pulls up on a rain-slick street. The girl steps forward, caught in slight motion blur; the boy stays behind, looking down. Streetlights and reflections glow softly with {color_noun} highlights.
Mood: Bittersweet, quiet heartbreak.
Text:
Boy (manga font): “See you.”
(handwritten gray) He wished he could tell her.

Scene 5 – “After the Rain”
Visual: Close on the boy’s face. The rain has stopped. A faint golden light replaces the {color_noun} hue. Behind him, her umbrella rests forgotten on a bench.
Mood: Peaceful emptiness, quiet acceptance.
Text:
(handwritten gray) Even the air feels quieter without her.
""".strip()

    return header + scenes + "\n"


def is_portrait(item:dict)->bool:
    info=item.get("image") or {}
    try:
        w,h=int(info.get("width",0)),int(info.get("height",0)); return h>w and h>=600
    except Exception: return False

def is_allowed_link(url:str)->bool:
    if not EXT_RE.search(url or ""): return False
    try:
        host=urlparse(url).netloc.lower()
        if any(bad in host for bad in BAD_DOMAINS): return False
        host=host.split(":")[0]
        return any(host==d or host.endswith("."+d) for d in PREFERRED_DOMAINS)
    except Exception: return False

def search_images(topic:str)->List[str]:
    service = build("customsearch","v1",developerKey=GOOGLE_API_KEY)
    queries = [
        f'{topic} anime official key visual portrait poster site:ghibli.jp OR site:theposterdb.com OR site:crunchyroll.com OR site:aniplex.co.jp OR site:storyblok.com',
        f"{topic} anime key visual HD OR high resolution OR movie poster",
        f"{topic} anime vertical portrait poster"
    ]
    seen,best=set(),[]
    for q in queries:
        for attempt in range(RETRY_COUNT):
            try:
                resp = service.cse().list(q=q,cx=GOOGLE_CX_ID,searchType="image",num=10,safe="active",imgType="photo").execute()
                items = resp.get("items",[]) or []
                for it in items:
                    link=it.get("link")
                    if not link or link in seen: continue
                    if is_portrait(it) and is_allowed_link(link):
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

SEED_TOPICS=[
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

def append_new_idea_rows(ws,hdr:dict,n:int=5):
    added=0
    for topic in SEED_TOPICS:
        if added>=n: break
        tone=infer_tone(topic); cap=caption_prompt(topic,tone); tags=hashtag_prompt(topic)
        values=["Ready",topic,"Link","","",tone,cap,tags,"","","To do"]
        ws.append_row(values,value_input_option="RAW"); added+=1; time.sleep(WRITE_SLEEP)
    if added: print(f"🆕 Appended {added} new idea rows.")

def row_is_fully_empty(row:List[str],hdr:dict)->bool:
    for idx in [hdr[H_TOPIC],hdr[H_SOURCE],hdr[H_PROMPT],hdr[H_LINKS],hdr[H_TONE],hdr[H_CAPTION],hdr[H_HASHTAG],hdr[H_FINAL],hdr[H_AI_LINKS],hdr[H_ASSIST]]:
        val=row[idx-1] if len(row)>=idx else ""
        if str(val).strip(): return False
    return True

def process():
    ws=get_ws(); hdr=header_map(ws); rows=ws.get_all_values()[1:]
    updated=0; empties_detected=0
    for r_idx,row in enumerate(rows,start=2):
        try:
            assistant=(row[hdr[H_ASSIST]-1] if len(row)>=hdr[H_ASSIST] else "").strip()
            if assistant=="Done": continue
            if row_is_fully_empty(row,hdr): empties_detected+=1; continue

            topic=(row[hdr[H_TOPIC]-1] if len(row)>=hdr[H_TOPIC] else "").strip()
            source_raw=(row[hdr[H_SOURCE]-1] if len(row)>=hdr[H_SOURCE] else "")
            source=source_raw.strip().lower()
            if not topic: continue

            if source=="link":
                tone=infer_tone(topic); links=search_images(topic)
                ws.update_cell(r_idx,hdr[H_TONE],tone)
                ws.update_cell(r_idx,hdr[H_CAPTION],caption_prompt(topic,tone))
                ws.update_cell(r_idx,hdr[H_HASHTAG],hashtag_prompt(topic))
                if links: ws.update_cell(r_idx,hdr[H_LINKS],", ".join(links))
                ws.update_cell(r_idx,hdr[H_ASSIST],"Done" if len(links)==3 else "Needs Images")
                updated+=1; time.sleep(WRITE_SLEEP)

            elif source=="ai":
                tone=infer_tone(topic); prompt=ai_image_prompt(topic)
                ws.update_cell(r_idx,hdr[H_PROMPT],prompt)
                ws.update_cell(r_idx,hdr[H_TONE],tone)
                ws.update_cell(r_idx,hdr[H_CAPTION],caption_prompt(topic,tone))
                ws.update_cell(r_idx,hdr[H_HASHTAG],hashtag_prompt(topic))
                ws.update_cell(r_idx,hdr[H_ASSIST],"Done")
                updated+=1; time.sleep(WRITE_SLEEP)

        except Exception as e:
            print(f"❌ Error on row {r_idx}: {e}")

        if updated>=MAX_ROWS_PER_RUN:
            print(f"ℹ️ Reached MAX_ROWS_PER_RUN={MAX_ROWS_PER_RUN}")
            break

    if empties_detected:
        append_new_idea_rows(ws,hdr,n=min(empties_detected,5))

    print(f"Done. Updated rows: {updated}. Empty rows spotted: {empties_detected}")

if __name__=="__main__": process()
