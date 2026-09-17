"""Google Gemini API calls: reading keys off a screenshot, semantic matching, translation.

All three functions ask the model for JSON-only output and parse it. Models
occasionally wrap JSON in prose or a markdown fence despite instructions, so
_extract_json strips that before parsing.
"""
import json
import re
import time

from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-3.6-flash"

_TRANSIENT_RETRY_DELAYS = (2, 5, 10)  # seconds, for 503/overload/429 responses


class AIError(Exception):
    pass


def _extract_json(text: str):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise AIError(f"Model didn't return valid JSON: {e}\n\nRaw output:\n{text[:800]}")


def _call(api_key: str, model: str, contents: list, max_tokens: int = 8192) -> str:
    client = genai.Client(api_key=api_key)
    last_error = None
    for attempt, delay in enumerate((0,) + _TRANSIENT_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            if not response.text:
                raise AIError("Gemini returned no output (the request may have been blocked).")
            return response.text
        except AIError:
            raise
        except Exception as e:
            msg = str(e)
            if "API_KEY_INVALID" in msg or "API key not valid" in msg or "PERMISSION_DENIED" in msg:
                raise AIError("Google rejected the API key. Check it in the sidebar.")
            is_transient = "503" in msg or "UNAVAILABLE" in msg or "429" in msg or "RESOURCE_EXHAUSTED" in msg
            last_error = AIError(f"Gemini API error: {e}")
            if not is_transient:
                raise last_error
    raise last_error


def extract_keys_from_screenshot(
    image_bytes: bytes, media_type: str, api_key: str, model: str = DEFAULT_MODEL
) -> list[dict]:
    """Read placeholder content-key strings off a staging-page screenshot or PDF."""
    prompt = (
        "This is a screenshot (or a full-page PDF printout) of a staging "
        "webpage that is showing raw placeholder content-key strings instead "
        "of real text (e.g. \"hero-block.title.text\", "
        "\"cta-section.button.label\"). If this is a multi-page PDF, treat it "
        "as one continuous page and number reading order across all pages. "
        "Find every placeholder-key-looking string visible "
        "(usually dot- or dash-separated lowercase identifiers). For each one, "
        "report its exact text, its rank in top-to-bottom, left-to-right reading "
        "order (1-indexed, continuous across the whole document), and a short guess "
        "of its visual role in the layout, e.g. \"large hero heading\", "
        "\"subheading\", \"body paragraph\", \"button label\", \"stat number\", "
        "\"nav link\", \"image caption\".\n\n"
        "Respond with ONLY a JSON array, no prose, no markdown fence:\n"
        '[{"key": "hero-block.title.text", "order": 1, "role": "large hero heading"}, ...]'
    )
    contents = [types.Part.from_bytes(data=image_bytes, mime_type=media_type), prompt]
    raw = _call(api_key, model, contents)
    result = _extract_json(raw)
    if not isinstance(result, list):
        raise AIError("Expected a JSON array of keys from the vision step.")
    return result


def match_keys_to_figma(
    keys: list[dict], figma_nodes: list[dict], api_key: str, model: str = DEFAULT_MODEL
) -> dict:
    """Semantically match extracted placeholder keys to Figma text nodes.

    Uses page order + visual role as the primary signals (not raw pixel
    position), since the Figma design can contain more sections/decorative
    content than the live page, which makes strict index alignment unreliable.
    """
    figma_summary = [
        {
            "id": n["id"],
            "reading_order": n["reading_order"],
            "font_size": n["font_size"],
            "font_weight": n["font_weight"],
            "layer_name": n["name"],
            "text": n["characters"][:300],
        }
        for n in figma_nodes
    ]
    keys_summary = [{"key": k.get("key"), "order": k.get("order"), "role": k.get("role")} for k in keys]

    prompt = (
        "You are matching a live webpage's placeholder content keys to the "
        "Figma design text layers they should be replaced with.\n\n"
        "FIGMA_TEXT_NODES (ordered top-to-bottom by reading_order, with font "
        "size/weight as a proxy for visual role — larger/bolder text is "
        "typically a heading):\n"
        f"{json.dumps(figma_summary, ensure_ascii=False)}\n\n"
        "PLACEHOLDER_KEYS (ordered top-to-bottom by order, with a guessed "
        "visual role from a screenshot):\n"
        f"{json.dumps(keys_summary, ensure_ascii=False)}\n\n"
        "Match each key to the Figma text node it most likely corresponds to. "
        "Use page order AND visual role (heading vs. body vs. button, font "
        "size/weight) as your primary signals — this is semantic/structural "
        "matching, not strict positional index alignment. The Figma design "
        "commonly has MORE sections and denser decorative content than the "
        "live page currently does, so the Nth key does not necessarily "
        "correspond to the Nth Figma node — use the key's own name (e.g. "
        "\"hero-block.title\" suggests a hero heading) and role together with "
        "order to find the best semantic fit. Some keys may have no good match "
        "in Figma, and some Figma nodes may have no corresponding key — leave "
        "those unmatched rather than forcing a low-quality pairing.\n\n"
        "Respond with ONLY this JSON object, no prose, no markdown fence:\n"
        "{\n"
        '  "matches": [\n'
        '    {"key": "...", "figma_node_id": "...", "figma_text": "...", '
        '"confidence": "high|medium|low", "reasoning": "short reason"}\n'
        "  ],\n"
        '  "unmatched_keys": ["..."],\n'
        '  "unmatched_figma_node_ids": ["..."]\n'
        "}"
    )
    raw = _call(api_key, model, [prompt])
    result = _extract_json(raw)
    if not isinstance(result, dict) or "matches" not in result:
        raise AIError("Expected a JSON object with a 'matches' array from the matching step.")
    return result


def translate_rows(
    texts: list[str], target_langs: list[str], api_key: str, model: str = DEFAULT_MODEL
) -> list[dict]:
    """Translate a list of English strings into each of target_langs (ISO codes)."""
    lang_names = {"de": "German", "fr": "French"}
    langs_desc = ", ".join(f'"{l}" ({lang_names.get(l, l)})' for l in target_langs)
    prompt = (
        "Translate each English UI copy string below into each of these "
        f"target languages: {langs_desc}. Preserve tone and length appropriate "
        "for UI copy (headings stay short, buttons stay short).\n\n"
        f"STRINGS (JSON array, translate in this exact order):\n{json.dumps(texts, ensure_ascii=False)}\n\n"
        "Respond with ONLY a JSON array, same length and order as the input, "
        "no prose, no markdown fence:\n"
        '[{"' + '": "...", "'.join(target_langs) + '": "..."}, ...]'
    )
    raw = _call(api_key, model, [prompt])
    result = _extract_json(raw)
    if not isinstance(result, list) or len(result) != len(texts):
        raise AIError("Translation step didn't return one result per input string.")
    return result
