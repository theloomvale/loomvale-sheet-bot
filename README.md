# Loomvale Sheet Bot (Final)

**Sheet columns (A→I)**  
A Status | B Topic | C ImageSource | D SourceLinks | E ImagePrompt_Ambience | F ImagePrompt_Scenes | G Tone | H Caption+Hashtags Prompt | I Assistant

- Processes **5 rows/run** (cron: every 2 days @ 09:00 UTC + manual run).  
- **Link rows:** finds up to 3 portrait (h≥800) URLs from trusted domains (official→reputable→Pinterest). If <3 → Assistant = “Couldn't find images” (bot retries next runs).  
- **AI rows:** writes Ambience + 5 Scenes (Hugging Face readable), Tone, merged Caption+Hashtags prompt, Assistant = “Done”.  
- **Fully empty rows:** filled **in place** with nerdy ideas; chooses AI vs Link and completes fields.

## Required Secrets
- `GOOGLE_CREDENTIALS_JSON` (service account JSON, raw or base64)
- `SHEET_ID` (spreadsheet key)
- `GOOGLE_API_KEY` (Google CSE JSON API)
- `GOOGLE_CX_ID` (Programmable Search Engine ID)

## Local test
```bash
export GOOGLE_CREDENTIALS_JSON='…'
export SHEET_ID='…'
export GOOGLE_API_KEY='…'
export GOOGLE_CX_ID='…'
python3 loomvale_sheet_bot.py
```
