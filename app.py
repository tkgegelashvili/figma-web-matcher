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

st.markdown(
    """
    <style>
    .stApp { background: #0a0a0a; }
    [data-testid="stSidebar"] { background: #0a0a0a; border-right: 1px solid #2a2a2a; }
    h1 {
        text-align: center;
        letter-spacing: 0.02em;
        margin-bottom: 0.2em;
    }
    .fwm-subtitle {
        text-align: center;
        color: #888;
        max-width: 640px;
        margin: 0 auto 2.2em auto;
        line-height: 1.6;
    }
    .fwm-eyebrow {
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: #666;
        font-size: 0.75em;
        margin-bottom: 1.5em;
    }
    div[data-testid="stTextInput"] input,
    div[data-testid="stTextArea"] textarea {
        background: #111 !important;
        border: 1px solid #2a2a2a !important;
        border-radius: 6px !important;
        color: #e5e5e5 !important;
    }
    div[data-testid="stTextInput"] input::placeholder { color: #555 !important; }
    [data-testid="stFileUploaderDropzone"] {
        background: #111 !important;
        border: 1px dashed #333 !important;
        border-radius: 6px !important;
    }
    div[data-testid="stButton"] button, div[data-testid="stFormSubmitButton"] button {
        border-radius: 999px !important;
        border: 1px solid #333 !important;
        background: #141414 !important;
        color: #e5e5e5 !important;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-size: 0.8em;
    }
    div[data-testid="stButton"] button:hover {
        border-color: #666 !important;
        color: #fff !important;
    }
    button[kind="primary"] {
        background: #e5e5e5 !important;
        color: #0a0a0a !important;
        border: 1px solid #e5e5e5 !important;
    }
    hr { border-color: #2a2a2a !important; }
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
        gemini_key = st.text_input(
            "Gemini API key",
            type="password",
            help="Free at aistudio.google.com -> Get API key.",
        )
        figma_token = st.text_input("Figma personal access token", type="password")
    model = st.text_input(
        "Gemini model ID",
        value=DEFAULT_MODEL,
        help="Change this if Google renames/retires the default model.",
    )

st.markdown('<div class="fwm-eyebrow" style="text-align:center;">CONTENT MATCHING</div>', unsafe_allow_html=True)
st.title("Figma → Web Content Matcher")
st.markdown(
    '<div class="fwm-subtitle">Match a staging page\'s placeholder content keys to the '
    "real copy in your Figma design. Reviewed by a human, exported CMS-ready.</div>",
    unsafe_allow_html=True,
)

category = st.text_input(
    "Category",
    placeholder="e.g. dropshipping-stores",
    help="CMS content category — applied to every row extracted from this batch.",
)

col1, col2 = st.columns(2)
with col1:
    figma_url = st.text_input(
        "Figma file or frame link",
        placeholder="https://www.figma.com/design/abc123/My-File?node-id=1-23",
    )
with col2:
    screenshot = st.file_uploader(
        "Screenshot (or full-page PDF) of the staging page (showing placeholder keys)",
        type=["png", "jpg", "jpeg", "pdf"],
        help="A PDF from your browser's Print -> Save as PDF usually captures the "
        "whole page, not just what's visible on screen — use that for long pages.",
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
