# Loomvale Sheet Bot

Automates the creative content pipeline for the Loomvale brand.

### Overview
This bot:
- Reads the Google Sheet tab named **"Pipeline"**
- Fills image prompts, tones, captions, and hashtags
- Finds anime-related key visuals (for Link posts)
- Generates cinematic AI prompts (for AI posts)
- Restarts the Hugging Face image generator Space after each batch

---

### Environment Variables (Secrets)
| Name | Description |
|------|--------------|
| `GOOGLE_API_KEY` | Google Custom Search API key |
| `GOOGLE_CX_ID` | Programmable Search Engine CX ID |
| `GOOGLE_CREDENTIALS_JSON` | Service account credentials JSON (stringified) |
| `SHEET_ID` | Google Sheet ID |
| `HF_TOKEN` | Hugging Face API token |
| `HF_SPACE_URL` | URL to your Hugging Face Space (e.g. https://huggingface.co/spaces/Theloomvale/loomvale-image-lab) |

---

### Schedule
- Runs automatically every **2 days at 09:00 UTC**
- Can be **triggered manually** under the “Actions” tab

---

### Output
- Updates your Sheet in place.
- `Assistant` column will show:
  - `Done` — processed successfully  
  - `Couldn't find images` — link search failed  
  - `Error` — something went wrong  
