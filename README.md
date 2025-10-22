# Loomvale Sheet Bot

Automates the **Pipeline** Google Sheet:

**Columns (A→J):**

A Status | B Topic | C ImageSource | D SourceLinks | E ImagePrompt_Ambience | F ImagePrompt_Scenes | G AI generated images | H Tone | I Caption+Hashtags Prompt | J Assistant

## What it does

- Processes up to **5 rows per run**.
- If a row is **fully empty** (B..J), the bot **creates a new post idea**:
  - 60% **AI** → fills E (Ambience) + F (5 Scenes), H (Tone), I (Caption+Hashtags), sets J = `Needs AI Images`, leaves D/G empty.
  - 40% **Link** → finds up to 3 portrait poster URLs (D), fills H/I, sets J = `Done` (3 links) or `Couldn't find images`, leaves E/F/G empty.
- For existing rows:
  - **AI** → fills missing Ambience/Scenes/Caption prompt; sets J = `Needs AI Images`.
  - **Link** → finds portrait URLs if missing; sets J accordingly; fills Tone & Caption prompt.

## Setup

### GitHub secrets

- `SHEET_ID` – your Google Sheet ID
- `PIPELINE_TAB` – (optional) tab name, default `Pipeline`
- `GOOGLE_CREDENTIALS_JSON` – service account JSON (raw or base64)
- `GOOGLE_API_KEY` – Google Custom Search JSON API key
- `GOOGLE_CX_ID` – Custom Search Engine ID (image search enabled, limited to your domains)

### Run

- Manual: **Actions → Loomvale Sheet Bot → Run workflow**
- Auto: every **2 days @ 09:00 UTC** (see cron)

### Notes

- Portrait filter: vertical (h > w) and height ≥ 800, whitelisted/official domains preferred (Pinterest allowed as fallback).
- `I` merges caption + hashtags request **(directions only)** so your generation layer can produce final text & tags.
