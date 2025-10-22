# Loomvale Sheet Bot (new layout)

Automates your Google Sheet **Pipeline** tab with two post types:

- **Link** rows: finds up to **3 portrait** poster/key-visual URLs from reputable domains via Google CSE and writes them to **D: SourceLinks**.
- **AI** rows: waits for prompts (E/F), then calls your Hugging Face Space to generate **5 images** and writes their URLs to **G: AI generated images**.

## Sheet columns (A→J)

A `Status`  
B `Topic`  
C `ImageSource` ("AI" or "Link")  
D `SourceLinks` (Link rows)  
E `ImagePrompt_Ambience` (Assistant fills)  
F `ImagePrompt_Scenes` (Assistant fills)  
G `AI generated images` (this bot writes 5 URLs)  
H `Tone` (bot fills)  
I `Caption+Hashtags Prompt` (bot fills – *directions only*)  
J `Assistant` (state: Needs Prompts / Needs Images / Done / Couldn't find images / Error)

## State machine

- **AI rows**
  - If E or F is empty → `Assistant = Needs Prompts` (the OpenAI Assistant should fill E/F)
  - If E+F present and G empty → bot calls your HF Space → writes 5 URLs in **G** → `Done`
- **Link rows**
  - Bot writes up to 3 portrait URLs to **D**
  - 3 found → `Done`; fewer → `Couldn't find images`

## Secrets (GitHub → Settings → Secrets and variables → Actions)

- `GOOGLE_CREDENTIALS_JSON` — service account JSON
- `SHEET_ID`
- `WORKSHEET_NAME` — e.g., `Pipeline`
- `GOOGLE_API_KEY` + `GOOGLE_CX_ID` — optional (for Link rows via Google CSE)
- `HF_SPACE_URL` — e.g. `https://huggingface.co/spaces/Theloomvale/loomvale-image-lab`
- `HF_TOKEN` — optional; required if Space is private
- `MAX_ROWS_PER_RUN` — optional (default `5`)

## Run

- Manual: **Actions → Run workflow**
- Scheduled: every **2 days at 09:00 UTC** (edit in `.github/workflows/loomvale-cron.yml`)

## Notes

- Bot never overwrites A/B/C.
- For AI rows it **waits** for prompts; it does not generate them. Your OpenAI Assistant should write **E** and **F** first.
- The HF Space should either return permanent HTTP links in its API response or upload images to the Space repo and return those URLs. (Your latest Space does this.)
