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
"mantan-web.jp","dengekionline.com","kadokawa.co.jp","fuji.tv","bandainamcoent.co.jp","netflix.com",
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

def _color_word(color: str) -> str:
    return {
        "Mizu blue": "blue",
        "Soft sage green": "green",
        "War lantern orange": "orange",
        "Karma beige": "beige",
        "Charcoal gray": "gray"
    }.get(color, "blue")

def _detect_archetype(topic: str) -> str:
    t = topic.lower()
    if any(k in t for k in ["cat", "kitten", "dog", "pet", "lofi", "cozy", "desk", "nap", "coffee", "studio", "room"]):
        return "cozy"
    if any(k in t for k in ["spirit", "forest", "shrine", "ghibli", "wind", "river", "kami", "mountain", "fox", "kitsune"]):
        return "fantasy"
    if any(k in t for k in ["city", "neon", "cyberpunk", "night", "train", "shibuya", "shinjuku", "downtown", "rain"]):
        return "urban"
    if any(k in t for k in ["romance", "love", "kiss", "goodbye", "letter", "memory", "home", "heart"]):
        return "romance"
    if any(k in t for k in ["ai", "design", "hologram", "holographic", "ux", "ui", "tech", "studio", "render", "digital"]):
        return "tech"
    return "cozy"

def ai_image_prompt(topic: str) -> str:
    color = select_brand_color(topic)
    cw = _color_word(color)
    arche = _detect_archetype(topic)

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

    if arche == "cozy":
        scenes = f"""
Scene 1 – “Soft Start”
Visual: Sun-dusted room; {topic} elements on a wooden desk; a sleepy companion (cat or plant) sharing the moment; {cw} accents in textiles and small objects.
Mood: quiet comfort and gentle focus.
Text:
(manga font): "five more minutes."
(handwritten gray) warm light, slower time.

Scene 2 – “Ambient Motion”
Visual: Curtain moves in a breeze; dust motes floating; kettle steam curling; {cw} tint feathered across walls.
Mood: cozy routine.
Text:
(handwritten gray) repetition makes its own music.

Scene 3 – “Intimate Focus”
Visual: close-up of hands, pen, or keys; cat paw over notebook; small stickers; {cw} glow from desk lamp.
Mood: tender concentration.
Text:
(manga font): "stay."
(handwritten gray) you make silence feel kinder.

Scene 4 – “Pause”
Visual: a yawn; half-finished sketch; tea ring on paper; window light shifts toward {cw}.
Mood: stillness, tiny exhale.
Text:
(handwritten gray) resting is part of the work.

Scene 5 – “Afterglow”
Visual: the room holds warmth; the companion sleeps; {cw} afterlight on the floor.
Mood: contented closure.
Text:
(handwritten gray) what remains is the gentle part.
""".strip()
    elif arche == "fantasy":
        scenes = f"""
Scene 1 – “Forest Breath”
Visual: dawn mist; shrine gate or mossy steps; leaf edges glowing {cw}; small spirits barely visible.
Mood: reverent calm.
Text:
(handwritten gray) the forest keeps a softer language.

Scene 2 – “Whispered Path”
Visual: character walks beneath tall trees; paper charms flicker; streams of {cw} light between trunks.
Mood: wonder, listening.
Text:
(manga font): "did you hear that?"
(handwritten gray) not sound—feeling.

Scene 3 – “Companion”
Visual: fox or small kami approaches; eyes luminous; cloth or hair rim-lit {cw}.
Mood: trust forming.
Text:
(handwritten gray) some promises don’t need words.

Scene 4 – “Crossing”
Visual: rope bridge or stepping stones; wind picks up; leaves spiral in {cw} ribbons.
Mood: threshold moment.
Text:
(manga font): "i'm ready."
(handwritten gray) the path agrees.

Scene 5 – “Offering”
Visual: quiet altar; water bowl reflecting {cw}; character leaves a paper wish.
Mood: humble gratitude.
Text:
(handwritten gray) the forest answered by staying.
""".strip()
    elif arche == "urban":
        scenes = f"""
Scene 1 – “Neon Drift”
Visual: rain-polished street; umbrella edge; {cw} neon reflecting in puddles; distant train bell.
Mood: cinematic solitude.
Text:
(handwritten gray) the city hums below your heartbeat.

Scene 2 – “Platforms”
Visual: station platform; flicker of signs; {cw} highlights on wet tiles; passing faces blur.
Mood: in-between time.
Text:
(manga font): "last train?"
(handwritten gray) the answer was always maybe.

Scene 3 – “Crosswalk Constellations”
Visual: overhead angle; umbrellas like dots; long shadows; {cw} streams across asphalt.
Mood: quiet choreography.
Text:
(handwritten gray) everyone carries a weather.

Scene 4 – “Side Street”
Visual: vending machine glow; steam from a stall; hand touches a note, edges lit {cw}.
Mood: close, human scale.
Text:
(manga font): "keep this."
(handwritten gray) proof that you were here.

Scene 5 – “Window Ride”
Visual: interior of moving train; window streaks; city dissolves into {cw} bokeh.
Mood: reflective release.
Text:
(handwritten gray) arrival can be a feeling.
""".strip()
    elif arche == "romance":
        scenes = f"""
Scene 1 – “Small Distance”
Visual: two figures share space but not touch; table for two; {cw} ribbon of light across faces.
Mood: delicate hesitation.
Text:
(manga font): "did you save me a seat?"
(handwritten gray) we saved each other a moment.

Scene 2 – “Shared Object”
Visual: one cup, two hands; napkin note; {cw} warmth on knuckles.
Mood: tender alignment.
Text:
(handwritten gray) even the quiet had a pulse.

Scene 3 – “Almost”
Visual: hands almost meet; breath fog on glass; {cw} halo behind them.
Mood: ache and glow.
Text:
(manga font): "say it first."
(handwritten gray) the word kept choosing silence.

Scene 4 – “Goodbye Weight”
Visual: doorway or gate; motion blur; {cw} spills over the threshold.
Mood: bittersweet release.
Text:
(handwritten gray) i folded the moment like paper.

Scene 5 – “Keepsake”
Visual: the left item: umbrella, scarf, page—left behind; {cw} light rests on it.
Mood: soft ache, acceptance.
Text:
(handwritten gray) what remains is true.
""".strip()
    else:  # tech / creative
        scenes = f"""
Scene 1 – “Studio Glow”
Visual: late-night desk; monitor hum; {cw} code or design grid reflected in glass.
Mood: focused calm.
Text:
(handwritten gray) the problem was a door.

Scene 2 – “Material Study”
Visual: pencils, tablets, tessellations; {cw} accents along edges; cursor trails.
Mood: curiosity.
Text:
(manga font): "what if—"
(handwritten gray) iteration is a kind of prayer.

Scene 3 – “Synthesis”
Visual: layered mockups; holographic overlay in {cw}; ideas click.
Mood: flow state.
Text:
(handwritten gray) the shape learned my hands.

Scene 4 – “Test Light”
Visual: prototype moving; soft motion blur; {cw} ghost-images track the path.
Mood: hopeful proof.
Text:
(manga font): "again."

Scene 5 – “Publish Quiet”
Visual: screen fades to ambient; coffee ring; notebook closed; {cw} afterglow.
Mood: satisfied exhale.
Text:
(handwritten gray) ship small, ship often.
""".strip()

    return header + scenes.replace("{topic}", topic).replace("{cw}", cw) + "\n"


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
