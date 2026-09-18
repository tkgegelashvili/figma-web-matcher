"""Figma -> Web Content Matcher

Streamlit app: a non-developer uploads a Figma file/frame link + token and a
screenshot of a staging page still showing raw placeholder content keys. The
app matches each key to the Figma text it should become, lets a human review
and correct the matches, optionally translates to German/French, and exports
CSV/Excel.
"""
import pandas as pd
import streamlit as st

from ai_client import DEFAULT_MODEL, AIError, extract_keys_from_screenshot, match_keys_to_figma, translate_rows
from exporter import to_csv_bytes, to_excel_bytes
from figma_client import FigmaError, fetch_figma_text_nodes, parse_figma_url

st.set_page_config(page_title="Figma -> Web Content Matcher", layout="wide")

THEMES = {
    "Dark": dict(bg="#0a0a0a", bg2="#141414", input_bg="#111111", text="#e5e5e5",
                 muted="#888888", muted2="#666666", border="#2a2a2a", border2="#333333",
                 placeholder="#555555", btn_hover="#666666", accent="#e5e5e5", accent_text="#0a0a0a"),
    "Grey": dict(bg="#2a2a2a", bg2="#333333", input_bg="#383838", text="#eaeaea",
                 muted="#bbbbbb", muted2="#999999", border="#4a4a4a", border2="#555555",
                 placeholder="#888888", btn_hover="#cccccc", accent="#eaeaea", accent_text="#2a2a2a"),
    "Light": dict(bg="#ffffff", bg2="#eeeeee", input_bg="#f7f7f7", text="#111111",
                  muted="#555555", muted2="#777777", border="#dcdcdc", border2="#bbbbbb",
                  placeholder="#999999", btn_hover="#111111", accent="#111111", accent_text="#ffffff"),
}

if "fwm_theme" not in st.session_state:
    st.session_state.fwm_theme = "Dark"

with st.sidebar:
    st.radio(
        "Theme", list(THEMES.keys()),
        horizontal=True,
        label_visibility="collapsed",
        key="fwm_theme",
    )

_t = THEMES[st.session_state.fwm_theme]

st.markdown(
    f"""
    <style>
    .stApp {{ background: {_t['bg']} !important; }}
    [data-testid="stSidebar"] {{ background: {_t['bg']} !important; border-right: 1px solid {_t['border']}; }}
    .stApp, .stApp *:not(input) {{ color: {_t['text']} !important; }}
    .stApp input[type="radio"], .stApp input[type="checkbox"] {{ accent-color: {_t['accent']} !important; }}
    h1 {{
        text-align: center;
        letter-spacing: 0.02em;
        margin-bottom: 0.2em;
    }}
    .fwm-subtitle, .fwm-subtitle * {{
        text-align: center;
        color: {_t['muted']} !important;
        max-width: 640px;
        margin: 0 auto 2.2em auto;
        line-height: 1.6;
    }}
    .fwm-eyebrow, .fwm-eyebrow * {{
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: {_t['muted2']} !important;
        font-size: 0.75em;
        margin-bottom: 1.5em;
    }}
    div[data-testid="stTextInput"] input,
    div[data-testid="stTextArea"] textarea {{
        background: {_t['input_bg']} !important;
        border: 1px solid {_t['border']} !important;
        border-radius: 6px !important;
        color: {_t['text']} !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06) !important;
    }}
    div[data-testid="stTextInput"] input::placeholder {{ color: {_t['placeholder']} !important; }}
    [data-testid="InputInstructions"] {{ display: none !important; }}
    [data-testid="stFileUploaderDropzone"] {{
        background: {_t['input_bg']} !important;
        border: 1px dashed {_t['border2']} !important;
        border-radius: 6px !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06) !important;
    }}
    [data-testid="stFileUploaderDropzone"] svg {{ fill: {_t['muted2']} !important; }}
    [data-testid="stFileUploaderDropzoneInstructions"] > div > span:first-child {{
        display: none !important;
    }}
    [data-testid="stFileUploaderDropzoneInstructions"] span {{
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: clip !important;
    }}
    /* File chip keeps its own native dark background in every theme (by
       design) - only force the text/icon to a fixed light color so it stays
       readable against that dark chip regardless of the page theme. Covers
       both stFileUploaderFile(Name/Icon) and the newer stFileChip(Name)
       testid, since local dev and Streamlit Cloud can run different
       Streamlit versions with different internal names. */
    [data-testid="stFileUploaderFile"], [data-testid="stFileUploaderFile"] *,
    [data-testid="stFileChip"], [data-testid="stFileChip"] * {{
        color: #e5e5e5 !important;
    }}
    [data-testid="stFileUploaderFileIcon"] svg, [data-testid="stFileChip"] svg {{
        fill: #999999 !important;
    }}
    /* Multiselect tags (e.g. translation language pills) always render on
       Streamlit's static primaryColor background regardless of our theme,
       with white text baked in - force readable dark text instead. */
    [data-baseweb="tag"], [data-baseweb="tag"] * {{ color: #0a0a0a !important; }}
    [data-testid="stMultiSelect"] [data-baseweb="select"] > div {{
        background: {_t['input_bg']} !important;
        border-color: {_t['border']} !important;
    }}
    .stApp button {{
        border-radius: 999px !important;
        border: 1px solid {_t['border2']} !important;
        background: {_t['bg2']} !important;
    }}
    .stApp button, .stApp button * {{
        color: {_t['text']} !important;
    }}
    div[data-testid="stButton"] button, div[data-testid="stFormSubmitButton"] button {{
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-size: 0.8em;
    }}
    [data-testid="stFileUploaderDropzone"] button {{
        border-radius: 8px !important;
        text-transform: none !important;
        letter-spacing: normal !important;
    }}
    .stApp button:hover {{
        border-color: {_t['btn_hover']} !important;
    }}
    [data-testid="baseButton-minimal"], button[title="Remove"] svg {{
        color: {_t['muted2']} !important;
        fill: {_t['muted2']} !important;
    }}
    button[kind="primary"] {{
        background: {_t['accent']} !important;
        border: 1px solid {_t['accent']} !important;
    }}
    button[kind="primary"], button[kind="primary"] * {{
        color: {_t['accent_text']} !important;
    }}
    hr {{ border-color: {_t['border']} !important; }}
    </style>
    """,
    unsafe_allow_html=True,
)

if "review_df" not in st.session_state:
    st.session_state.review_df = None


def _secret(name: str, default=None):
    try:
        return st.secrets[name]
    except Exception:
        return default


# If the app owner has configured GEMINI_API_KEY + FIGMA_TOKEN in Streamlit
# secrets, the app runs in "hosted" mode: those are used for every visitor
# and the sidebar credential fields disappear. Otherwise it falls back to
# each visitor entering their own (local/dev use). FIGMA_ALLOWED_FILE_KEYS
# restricts hosted mode to specific Figma file(s), so a baked-in token can't
# be used to read arbitrary files the owner happens to have access to.
_hosted_gemini_key = _secret("GEMINI_API_KEY")
_hosted_figma_token = _secret("FIGMA_TOKEN")
HOSTED_MODE = bool(_hosted_gemini_key and _hosted_figma_token)
_allowed_keys_raw = _secret("FIGMA_ALLOWED_FILE_KEYS", "")
FIGMA_ALLOWED_FILE_KEYS = {k.strip() for k in _allowed_keys_raw.split(",") if k.strip()}

with st.sidebar:
    st.header("Credentials")
    if HOSTED_MODE:
        st.caption("This app provides its own Gemini + Figma access — nothing to enter here.")
        gemini_key = _hosted_gemini_key
        figma_token = _hosted_figma_token
    else:
        st.caption("Used only for this session, in memory. Nothing is stored or logged.")
        gemini_key = st.text_input("Gemini API key", type="password")
        st.caption("Free at aistudio.google.com -> Get API key.")
        figma_token = st.text_input("Figma personal access token", type="password")
    model = st.text_input("Gemini model ID", value=DEFAULT_MODEL)
    st.caption("Change this if Google renames/retires the default model.")

st.markdown('<div class="fwm-eyebrow" style="text-align:center;">CONTENT MATCHING</div>', unsafe_allow_html=True)
st.title("Figma → Web Content Matcher")
st.markdown(
    '<div class="fwm-subtitle">Match a staging page\'s placeholder content keys to the '
    "real copy in your Figma design. Reviewed by a human, exported CMS-ready.</div>",
    unsafe_allow_html=True,
)

category = st.text_input("Category", placeholder="e.g. dropshipping-stores")
st.caption("CMS content category — applied to every row extracted from this batch.")

col1, col2 = st.columns(2)
with col1:
    figma_url = st.text_input(
        "Figma file or frame link",
        placeholder="https://www.figma.com/design/abc123/My-File?node-id=1-23",
    )
with col2:
    screenshot = st.file_uploader(
        "Screenshot (or full-page PDF) of the staging page",
        type=["png", "jpg", "jpeg", "pdf"],
    )
    st.caption(
        "A PDF from your browser's Print -> Save as PDF usually captures the "
        "whole page, not just what's visible on screen — use that for long pages."
    )

run = st.button("Extract & match", type="primary")

if run:
    if not gemini_key or not figma_token:
        st.error("Enter both your Gemini API key and Figma token in the sidebar first.")
    elif not figma_url or not screenshot:
        st.error("Provide a Figma link and a screenshot before running.")
    elif not category:
        st.error("Enter a Category for this batch before running.")
    else:
        try:
            with st.status("Running...", expanded=True) as status:
                st.write("Step 1/3 — Reading Figma file...")
                file_key, node_id = parse_figma_url(figma_url)
                if FIGMA_ALLOWED_FILE_KEYS and file_key not in FIGMA_ALLOWED_FILE_KEYS:
                    raise FigmaError(
                        "This app is only approved to read specific Figma file(s), "
                        "and that link isn't one of them."
                    )
                figma_nodes = fetch_figma_text_nodes(file_key, figma_token, node_id)
                st.write(f"Found {len(figma_nodes)} text layers in Figma.")

                st.write("Step 2/3 — Reading placeholder keys off the screenshot...")
                image_bytes = screenshot.getvalue()
                media_type = screenshot.type or "image/png"
                keys = extract_keys_from_screenshot(image_bytes, media_type, gemini_key, model)
                st.write(f"Found {len(keys)} placeholder keys in the screenshot.")

                st.write("Step 3/3 — Matching keys to Figma text...")
                result = match_keys_to_figma(keys, figma_nodes, gemini_key, model)
                st.write(f"Matched {len(result.get('matches', []))} of {len(keys)} keys.")

                status.update(label="Done", state="complete", expanded=False)

            matches = result.get("matches", [])
            rows = []
            for m in matches:
                rows.append(
                    {
                        "category": category,
                        "key": m.get("key", ""),
                        "matched_text_en": m.get("figma_text", ""),
                        "confidence": m.get("confidence", ""),
                        "reasoning": m.get("reasoning", ""),
                        "figma_node_id": m.get("figma_node_id", ""),
                        "include": True,
                    }
                )
            for k in result.get("unmatched_keys", []):
                rows.append(
                    {
                        "category": category,
                        "key": k,
                        "matched_text_en": "",
                        "confidence": "no match",
                        "reasoning": "No corresponding Figma text found.",
                        "figma_node_id": "",
                        "include": False,
                    }
                )

            st.session_state.review_df = pd.DataFrame(rows)
            st.success(
                f"Matched {len(matches)} of {len(keys)} keys. Review and correct below before exporting."
            )
        except (FigmaError, AIError) as e:
            st.error(str(e))

if st.session_state.review_df is not None:
    st.subheader("Review & correct matches")
    st.caption(
        "Edit \"matched_text_en\" directly to fix a wrong match. Uncheck "
        "\"include\" to drop a row from the export."
    )
    edited_df = st.data_editor(
        st.session_state.review_df,
        num_rows="dynamic",
        use_container_width=True,
        key="editor",
        column_config={
            "confidence": st.column_config.TextColumn(width="small"),
            "include": st.column_config.CheckboxColumn(width="small"),
        },
    )
    st.session_state.review_df = edited_df

    st.divider()
    st.subheader("Translations")
    st.caption("AI-generated — spot-check before shipping, don't treat as final.")
    target_langs = st.multiselect(
        "Translate matched text into:",
        ["de", "fr"],
        default=["de", "fr"],
        format_func=lambda l: {"de": "German", "fr": "French"}[l],
    )
    if st.button("Generate translations") and target_langs:
        if not gemini_key:
            st.error("Enter your Gemini API key in the sidebar first.")
        else:
            try:
                included = edited_df[edited_df["include"]]
                with st.spinner("Translating..."):
                    translations = translate_rows(
                        included["matched_text_en"].tolist(), target_langs, gemini_key, model
                    )
                for lang in target_langs:
                    col_name = f"text_{lang}"
                    edited_df.loc[included.index, col_name] = [t.get(lang, "") for t in translations]
                st.session_state.review_df = edited_df
                st.toast("Translations added — your file is ready to export below.", icon="✅")
                st.rerun()
            except AIError as e:
                st.error(str(e))

    st.divider()
    st.caption(
        "Exported columns: Category, Message (the placeholder key), en, de, fr. "
        "Run \"Generate translations\" above first if de/fr should be filled in."
    )
    export_df = edited_df[edited_df["include"]].rename(
        columns={"category": "Category", "key": "Message", "matched_text_en": "en"}
    )
    for lang in ("de", "fr"):
        col = f"text_{lang}"
        if col in export_df.columns:
            export_df = export_df.rename(columns={col: lang})
        else:
            export_df[lang] = ""
    export_df = export_df[["Category", "Message", "en", "de", "fr"]]
    dl1, dl2 = st.columns(2)
    with dl1:
        st.download_button(
            "Download CSV",
            data=to_csv_bytes(export_df),
            file_name="content-matches.csv",
            mime="text/csv",
        )
    with dl2:
        st.download_button(
            "Download Excel",
            data=to_excel_bytes(export_df),
            file_name="content-matches.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
