# ClearQuote: my take on "Kill the Quote Spreadsheet"

Reads messy vendor quotes (Excel, PDF, Word, photo, email) with Gemini, normalises them with plain code,
and lets a buyer interrogate the comparison in plain language.

**Design rule:** the model reads and plans; code computes. No total, conversion or comparison is ever produced by the model.

## Run it on your laptop (about 10 minutes)

1. Get a free key: open aistudio.google.com, sign in, click "Get API key". No card needed.
2. Install Python 3.11 or newer, then in a terminal inside this folder:
       pip install -r requirements.txt
3. Put the key in a file called `.streamlit/secrets.toml` (copy `secrets.toml.example`):
       GEMINI_API_KEY = "your key"
4. Start:
       streamlit run app.py
5. In the browser: tab 1 "Load sample RFx", tab 2 "Load the 5 sample responses" then "Read responses with AI",
   tab 3 to compare and ask, tab 4 to resolve flags.

## Check it against the answer key
Open `ANSWER_KEY.xlsx` (the Normalized and Split award sheets). Each vendor column in the app should match it
once you accept the flags in tab 4. The key's eligible split award total is the number to hit.

## Tests (no AI needed)
    pip install pytest
    python tests/test_pipeline.py

## If something breaks
- Model name: the default is `gemini-3.8-flash` (2.5-flash was retired for new keys). Check AI Studio for the current Flash model name and set
  `GEMINI_MODEL` (an environment variable) if it differs.
- Rate limit errors (429): wait a minute. Results are cached by file hash in `.cache/`, so a re-run is free.
- A vendor reads wrong: check `extract.py` SYSTEM prompt first. Do not hardcode answers.

## Put it online (free)
1. Create a GitHub repo and push this folder. Commit `.cache/` after one good run so the sample loads instantly.
   Never commit `.streamlit/secrets.toml`.
2. share.streamlit.io, New app, pick the repo, main file `app.py`.
3. Advanced settings, Secrets: paste `GEMINI_API_KEY = "..."`.
4. Free apps sleep when idle. Open the link yourself a few minutes before the interview.

## What is where
- `extract.py`  model reads a file into structured data with sources (AI step 1)
- `normalize.py` units, currency, discounts, flags, eligibility (plain code)
- `analyst.py`  tools the analyst model can call (AI step 2); every number comes from here
- `app.py`      the screens
