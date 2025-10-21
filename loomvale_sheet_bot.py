#!/usr/bin/env python3
# Loomvale Sheet Bot - GitHub Ready Version
# Handles Google Sheets + CSE + AI character detection + pipeline logic
# Worksheet default: "Pipeline"

import base64, json, os, re, time, unicodedata
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

PREFERRED_DOMAINS={"crunchyroll.com","ghibli.jp","aniplex.co.jp","toho.co.jp","imdb.com","posterdb.com","theposterdb.com","toei.co.jp","viz.com","animatetimes.com","mantan-web.jp","dengekionline.com","kadokawa.co.jp","fuji.tv","bandainamcoent.co.jp","netflix.com","horrorsociety.com"}
EXT_RE=re.compile(r"\.(jpg|jpeg|png|webp)(?:$|\?)",re.IGNORECASE)
BRAND_COLORS=["Mizu blue","Soft sage green","War lantern orange","Karma beige","Charcoal gray"]

def _load_service_account_creds():
    scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
    if GOOGLE_CREDENTIALS_JSON:
        raw=GOOGLE_CREDENTIALS_JSON
        if not raw.startswith("{"):
            try: raw=base64.b64decode(raw).decode("utf-8")
            except Exception: pass
        info=json.loads(raw)
        return Credentials.from_service_account_info(info,scopes=scopes)
    raise RuntimeError("Missing GOOGLE_CREDENTIALS_JSON in environment.")

def get_ws():
    gc=gspread.authorize(_load_service_account_creds())
    sh=gc.open_by_key(SHEET_ID)
    return sh.worksheet(WORKSHEET_NAME) if WORKSHEET_NAME else sh.sheet1

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
            "Start with a short emotional hook, then 2–3 concise lines, end with a subtle CTA ('save for later').\n"
            "Max 300 chars, ≤2 emojis, no hashtags.")

def hashtag_prompt(topic:str)->str:
    return ("20 lowercase keyword tags (comma-separated, no #). Focus on tech/ai/design terms and aesthetics."
            if any(k in normalize(topic) for k in ["ai","design","ux","ui","ar","vr","3d","tool"]) else
            "10 hashtags with # included, space-separated, mixing franchise, genre, and aesthetic.")

def infer_character_description(topic:str)->str:
    t=normalize(topic)
    if any(k in t for k in ["couple","love","romance","kiss","date"]): return "one boy and one girl, gentle romantic atmosphere"
    if any(k in t for k in ["girls","sisters","friends","schoolgirls","maidens","magical girls","idols"]): return "two girls with warm connection, cozy friendship mood"
    if any(k in t for k in ["boy","samurai","warrior","lone","hero"]): return "one boy protagonist, quiet determination"
    if any(k in t for k in ["group","team","band","class","guild"]): return "three to five mixed characters, cinematic group composition"
    if any(k in t for k in ["cat","neko","dog","fox","kitsune","spirit","tanuki"]): return "one human and one animal companion, spiritual and symbolic presence"
    return "single character focus, introspective moment"

def ai_image_prompt(topic:str)->str:
    color=BRAND_COLORS[int(time.time())%len(BRAND_COLORS)]
    characters=infer_character_description(topic)
    return (f"Create 5 cinematic images for: {topic}. Color theme: {color}.\n"
            f"Character composition: {characters}.\n"
            "Loomvale art direction: lo-fi, painterly, soft film-grain texture; soft, cinematic warmth; "
            "East Asian character type; manga-style dialogue with gray handwritten narration; "
            "text integrated naturally into the scene. Emphasize atmosphere, subtle motion, and cozy intimacy.")

def is_portrait(item:dict)->bool:
    info=item.get("image") or {}
    try:
        w,h=int(info.get("width",0)),int(info.get("height",0))
        return h>w and h>=600
    except Exception:
        return False

def is_allowed_link(url:str)->bool:
    if not EXT_RE.search(url or ""): return False
    try:
        host=urlparse(url).netloc.lower().split(":")[0]
        return any(host==d or host.endswith("."+d) for d in PREFERRED_DOMAINS)
    except Exception:
        return False

def search_images(topic:str)->List[str]:
    service=build("customsearch","v1",developerKey=GOOGLE_API_KEY)
    queries=[f"{topic} official key visual poster",f"{topic} key visual portrait",f"{topic} anime poster official",f"{topic}"]
    seen,best=set(),[]
    for q in queries:
        try:
            resp=service.cse().list(q=q,cx=GOOGLE_CX_ID,searchType="image",num=10,safe="active").execute()
            for it in (resp.get("items",[]) or []):
                link=it.get("link")
                if not link or link in seen: continue
                if is_portrait(it) and is_allowed_link(link):
                    best.append(link); seen.add(link)
                    if len(best)>=3: return best[:3]
            time.sleep(0.5)
        except Exception:
            continue
    return best[:3]

def process():
    ws=get_ws(); hdr={h:i+1 for i,h in enumerate(ws.row_values(1))}
    rows=ws.get_all_values()[1:]; updated=0
    for r_idx,row in enumerate(rows,start=2):
        try:
            assistant=(row[hdr["Assistant"]-1] if len(row)>=hdr["Assistant"] else "").strip()
            if assistant=="Done": continue
            topic=(row[hdr["Topic"]-1] if len(row)>=hdr["Topic"] else "").strip()
            source=(row[hdr["ImageSource"]-1] if len(row)>=hdr["ImageSource"] else "").strip()
            if not topic: continue
            if source.lower()=="link":
                tone=infer_tone(topic); links=search_images(topic)
                ws.update_cell(r_idx,hdr["Tone"],tone)
                ws.update_cell(r_idx,hdr["CaptionPrompt"],caption_prompt(topic,tone))
                ws.update_cell(r_idx,hdr["HashtagPrompt"],hashtag_prompt(topic))
                if links: ws.update_cell(r_idx,hdr["SourceLinks"],", ".join(links))
                ws.update_cell(r_idx,hdr["Assistant"],"Done" if len(links)==3 else "Needs Images")
                updated+=1
            elif source.lower()=="ai":
                tone=infer_tone(topic)
                ws.update_cell(r_idx,hdr["ImagePrompt"],ai_image_prompt(topic))
                ws.update_cell(r_idx,hdr["Tone"],tone)
                ws.update_cell(r_idx,hdr["CaptionPrompt"],caption_prompt(topic,tone))
                ws.update_cell(r_idx,hdr["HashtagPrompt"],hashtag_prompt(topic))
                ws.update_cell(r_idx,hdr["Assistant"],"Done")
                updated+=1
        except Exception as e:
            print(f"Error row {r_idx}: {e}")
    print(f"✅ Updated {updated} rows.")

if __name__=="__main__": process()
