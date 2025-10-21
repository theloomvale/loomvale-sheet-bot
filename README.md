# Loomvale Sheet Bot (GitHub Ready)

Automates your Google Sheet “Pipeline” tab.

### Includes
- **loomvale_sheet_bot.py** → main logic (AI prompt generation, tone inference, Google Sheets + CSE)
- **requirements.txt** → dependencies
- **_github/workflows/loomvale-cron.yml** → GitHub Actions workflow
- **README.md** → setup instructions

### Setup on GitHub
1. Upload all files to your repo.
2. Rename the `_github` folder to `.github` (GitHub recognizes workflows only in `.github/workflows/`).
3. Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret | Description |
|--------|--------------|
| `GOOGLE_CREDENTIALS_JSON` | Your service account JSON (raw or base64) |
| `SHEET_ID` | Google Sheet ID |
| `GOOGLE_API_KEY` | Custom Search API key |
| `GOOGLE_CX_ID` | Custom Search Engine ID |

### Run locally
```bash
pip install -r requirements.txt
export GOOGLE_CREDENTIALS_JSON='{"type":"service_account",...}'
export SHEET_ID=your_sheet_id
export GOOGLE_API_KEY=your_api_key
export GOOGLE_CX_ID=your_cx_id
python loomvale_sheet_bot.py
```
