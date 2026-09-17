# Figma -> Web Content Matcher

A Streamlit app for matching a staging page's raw placeholder content keys
(e.g. `hero-block.title.text`) to the real copy in a Figma design, so a
non-developer can review the mapping and hand engineering a CMS-ready file —
without anyone needing staging login credentials or write access to Figma.

## What it does

1. You paste a Figma file/frame link and your personal Figma access token.
2. You upload a screenshot, or a full-page PDF ("Print -> Save as PDF" from
   your browser, while logged in) of the staging page, showing raw key
   strings instead of real text. PDF is preferred for long pages, since a
   browser's print-to-PDF usually captures the whole page, not just the
   visible viewport.
3. The app:
   - Pulls all text layers + their position/size from the Figma file via the
     Figma REST API.
   - Uses an AI vision model to read the placeholder key strings off your
     screenshot, in reading order.
   - Uses an AI model to semantically match each key to the right Figma text,
     using page order and visual role (heading vs. button vs. body text) as
     signals — not raw pixel position (see "Why not position matching?"
     below).
4. You review and correct the matches in an editable table.
5. Optionally, generate German/French translations of the matched text.
6. Export the result as CSV or Excel.

## Setup

```bash
pip install -r requirements.txt
streamlit run app.py
```

By default each user enters their own credentials in the app's sidebar
(nothing is hardcoded or stored):
- A **Gemini API key** — free, no card required — from
  [aistudio.google.com](https://aistudio.google.com) -> "Get API key".
- A **Figma personal access token** (Figma -> Settings -> Personal access
  tokens).

To skip that for every visitor (e.g. a public deployment), see "Hosted mode"
below.

## Why a screenshot, not a live URL?

The staging site requires login (HTTP Basic Auth and/or an app-level login).
Rather than have this app store and use staging credentials — a real security
concern for anything shareable — the burden is shifted to the user: screenshot
the page yourself while already logged in in your own browser. This is a
deliberate design choice; please don't "fix" it by adding live URL fetching
with stored credentials.

## Why not position/index matching?

Position-based matching (1st section on the page <-> 1st section in Figma,
2nd <-> 2nd, etc.) was tried first and tested against real data — it failed
badly, because the Figma design typically has more decorative/dense content
(stat call-outs, extra sub-sections) than the live page's current key
structure, so section counts don't align and one mismatch cascades into wrong
matches for everything after it. This app uses semantic matching (page order
+ visual role, via an AI model) as the primary method instead. Please don't
simplify this back to pure position matching without re-testing against real
data first.

## Hosted mode (no credentials required from visitors)

For a public deployment where you don't want every visitor to need their own
Gemini + Figma keys, set `GEMINI_API_KEY` and `FIGMA_TOKEN` in Streamlit
secrets — see `.streamlit/secrets.toml.example` for the exact format. When
both are set, the app uses them for every visitor and hides the sidebar
credential fields entirely.

**Important:** a shared Figma token isn't scoped to one file — it can read
anything your Figma account can access. Once the app is public, anyone could
otherwise paste in a different Figma link and use your token to read files
you never intended to expose. Also set `FIGMA_ALLOWED_FILE_KEYS` (comma-separated)
to restrict hosted mode to specific file(s) — the app rejects any Figma link
whose file key isn't in that list. There's no equivalent risk for the Gemini
key (worst case is hitting a shared rate limit), so it's fine to share alone
even without a Figma allowlist, but sharing the Figma token without the
allowlist is not recommended for a genuinely public deployment.

Local hosted-mode testing: copy `.streamlit/secrets.toml.example` to
`.streamlit/secrets.toml` (gitignored, never commit it) and fill in real
values. On Streamlit Community Cloud, paste the same `key = "value"` lines
into the app's Settings -> Secrets panel instead — no file needed, and
nothing goes into the git repo.

## Deploying so it's shareable

### Option A: Streamlit Community Cloud (recommended)

1. Push this folder to a **private** GitHub repo.
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in with
   GitHub, and deploy the repo (main file: `app.py`).
3. For hosted mode (see above), add your secrets in the app's Settings ->
   Secrets panel after deploying. Otherwise, each visitor enters their own
   Gemini key + Figma token in the sidebar at runtime.

### Option B: Hugging Face Spaces

1. Create a new Space, SDK = Streamlit.
2. Push this folder's contents to the Space's repo (`app.py` at the root).
3. For hosted mode, add the same secrets via the Space's Settings ->
   Repository secrets (Spaces reads `st.secrets` from those, or you can add
   a `.streamlit/secrets.toml` directly in the Space if it stays private).

## Known limitations / possible follow-ups

- A plain screenshot only captures what's visible without scrolling. Use the
  PDF upload option for long pages instead (see above) — Gemini reads PDFs
  natively, page count included.
- AI-generated translations should be spot-checked, not treated as final.
- Hosted mode has no per-user rate limiting — heavy public traffic shares one
  Gemini quota and (for the allowlisted files) one Figma token's rate limit.

## Project layout

```
app.py            Streamlit UI + orchestration
figma_client.py    Figma REST API: URL parsing, TEXT node extraction
ai_client.py        Gemini calls: screenshot key extraction, matching, translation
exporter.py          CSV / Excel export
requirements.txt
```
