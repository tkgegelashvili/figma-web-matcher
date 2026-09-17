"""Fetch and parse TEXT nodes from a Figma file via the Figma REST API."""
import re
from typing import Optional
import requests

FIGMA_API_BASE = "https://api.figma.com/v1"


class FigmaError(Exception):
    pass


def parse_figma_url(url: str) -> tuple[str, Optional[str]]:
    """Extract (file_key, node_id) from a Figma file/frame URL.

    Handles both /file/<key>/... and /design/<key>/... URL forms, and
    converts the URL's node-id query param (e.g. "123-456") to the API's
    "123:456" form.
    """
    url = url.strip()
    match = re.search(r"figma\.com/(?:file|design)/([a-zA-Z0-9]+)", url)
    if not match:
        raise FigmaError(
            "Couldn't find a Figma file key in that URL. Expected something "
            "like https://www.figma.com/design/<key>/<name>?node-id=..."
        )
    file_key = match.group(1)

    node_id = None
    node_match = re.search(r"node-id=([^&]+)", url)
    if node_match:
        node_id = node_match.group(1).replace("-", ":")

    return file_key, node_id


def _walk_text_nodes(node: dict, out: list) -> None:
    if node.get("visible", True) is False:
        return
    if node.get("type") == "TEXT":
        box = node.get("absoluteBoundingBox") or {}
        style = node.get("style") or {}
        characters = node.get("characters", "")
        if characters.strip():
            out.append(
                {
                    "id": node.get("id"),
                    "name": node.get("name", ""),
                    "characters": characters,
                    "x": box.get("x", 0),
                    "y": box.get("y", 0),
                    "width": box.get("width", 0),
                    "height": box.get("height", 0),
                    "font_size": style.get("fontSize"),
                    "font_weight": style.get("fontWeight"),
                }
            )
    for child in node.get("children", []) or []:
        _walk_text_nodes(child, out)


def fetch_figma_text_nodes(file_key: str, token: str, node_id: Optional[str] = None) -> list[dict]:
    """Fetch all TEXT nodes under a file (or a specific frame, if node_id given).

    Returns a list of dicts sorted into approximate top-to-bottom reading
    order, each carrying a 0-based "reading_order" index.
    """
    headers = {"X-Figma-Token": token}

    if node_id:
        resp = requests.get(
            f"{FIGMA_API_BASE}/files/{file_key}/nodes",
            headers=headers,
            params={"ids": node_id},
            timeout=30,
        )
    else:
        resp = requests.get(f"{FIGMA_API_BASE}/files/{file_key}", headers=headers, timeout=30)

    if resp.status_code == 403:
        raise FigmaError("Figma rejected the token (403). Check the personal access token.")
    if resp.status_code == 404:
        raise FigmaError("Figma file not found (404). Check the file link.")
    if not resp.ok:
        raise FigmaError(f"Figma API error {resp.status_code}: {resp.text[:300]}")

    data = resp.json()

    roots = []
    if node_id:
        nodes_map = data.get("nodes", {})
        for entry in nodes_map.values():
            if entry and entry.get("document"):
                roots.append(entry["document"])
        if not roots:
            raise FigmaError("That node-id wasn't found in the file. Check the frame link.")
    else:
        roots = [data["document"]]

    text_nodes: list[dict] = []
    for root in roots:
        _walk_text_nodes(root, text_nodes)

    if not text_nodes:
        raise FigmaError("No text layers found in that file/frame.")

    # Approximate reading order: bucket by y (to tolerate small misalignment
    # between elements on the same visual row), then sort by x within a row.
    text_nodes.sort(key=lambda n: (round(n["y"] / 12), n["x"]))
    for i, n in enumerate(text_nodes):
        n["reading_order"] = i

    return text_nodes
