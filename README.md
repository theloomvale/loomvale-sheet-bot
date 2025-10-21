# Loomvale Sheet Bot — 5 Rows Per Run

- Processes at most **5 rows** each run (every 2 days @ 09:00 UTC) for higher-quality output.
- **AI rows:** always fill D (ImagePrompt) with a Loomvale 5-scene cinematic brief using ONE brand color per row; fill F/G/H; set K=Done.
- **Link rows:** improved image search (portrait/photo bias; prefers storyblok, media-amazon, ghibli, etc.); fill E + F/G/H; set K to Done (3 links) or Needs Images.
- Tolerant parsing of Column C values ("AI", "ai ", "Link", etc.).
- Fallback to **first sheet** if named tab is missing.

**Secrets (GitHub):** `GOOGLE_CREDENTIALS_JSON`, `SHEET_ID`, `GOOGLE_API_KEY`, `GOOGLE_CX_ID`.
