import html
import re

from dotenv import load_dotenv
import os

load_dotenv()

SUMMARY_MAX_LENGTH = int(os.getenv("SUMMARY_MAX_LENGTH", "200"))


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def summarize(text: str, max_length: int | None = None) -> str:
    limit = max_length or SUMMARY_MAX_LENGTH
    cleaned = " ".join(strip_html(text).split())
    if not cleaned:
        return "（本文なし）"
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"
