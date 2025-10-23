# Loomvale Sheet Bot

Cinematic, cozy, and organized. This bot keeps your **Google Sheet pipeline** filled with on-brand prompts, captions, and image links — and can generate **SDXL** images via Hugging Face.

**Follow us on IG:** [@theloomvale](https://instagram.com/theloomvale)  
**Get the Social App prompts pack:** [loomvale.gumroad.com/l/social-app](https://loomvale.gumroad.com/l/social-app)

## Sheet layout (exact headers, row 1)

A `Status` | B `Topic` | C `ImageSource` | D `SourceLinks` | E `ImagePrompt_Ambience` | F `ImagePrompt_Scenes` | G `AI generated images` | H `Tone` | I `Caption+Hashtag Prompt` | J `Assistant`

- **Link** rows → fills D with 3 portrait URLs (official sources first), writes H + I, leaves E/F/G empty.
- **AI** rows → fills E + F + H + I. If `HF_AUTOGEN=true`, also writes 5 drive URLs to G; otherwise sets J=`Generate Images` for the follow-up worker.

## Secrets (Repo → Settings → Secrets and variables → Actions)

- `SHEET_ID` (required)
- `PIPELINE_TAB` (e.g., `Pipeline`) or leave empty to use the first sheet
- `GOOGLE_CREDENTIALS_JSON` (service account JSON **content**)
- `GOOGLE_API_KEY` + `GOOGLE_CX_ID` (optional; for Link rows, Google CSE image search)
- `HF_TOKEN` (optional; for AI image generation)
- `HF_MODEL` (optional; default `stabilityai/stable-diffusion-xl-base-1.0`)
- `HF_AUTOGEN` (optional; `"true"` to generate inside the main run; default `"false"`)

Also share your Sheet with the **service account email** from your credentials as **Editor**.

## Workflows

- **Every 2 days:** The bot (loomvale-cron.yml) creates new ideas, fills prompts, and finds links. (pulus manual)
- **Every 30 minutes:** The HF worker (loomvale-hf.yml) generates AI images for pending rows.
- If image search fails → the bot still creates full posts with prompts, captions, and “Couldn’t find images” status. (pulus manual)
- If Hugging Face model is ready → the AI worker auto-fills drive URLs.


---
