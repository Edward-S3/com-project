from __future__ import annotations

from collections import defaultdict

from domain_store import extract_domain, normalize_domain

NAKABOSHI_DOMAIN = "nakaboshi.co.jp"

COLOR_DEFAULT = "#212121"
COLOR_NAKABOSHI = "#1565C0"
COLOR_TEAMS = "#1B7A1B"
COLOR_HIGHLIGHTED = "#B8860B"

TEAMS_SOURCES = {"Teams チャット", "Teams Team 投稿"}

SOURCE_TEAMS_POST = "Teams Team 投稿"
SOURCE_TEAMS_CHAT = "Teams チャット"
SOURCE_MAIL = "Outlook メール"


def is_teams_source(source: str) -> bool:
    return source in TEAMS_SOURCES


def resolve_row_color(source: str, sender_email: str, highlighted_domains: set[str]) -> str:
    domain = extract_domain(sender_email)
    if domain and domain in highlighted_domains:
        return COLOR_HIGHLIGHTED
    if is_teams_source(source):
        return COLOR_TEAMS
    if source == "Outlook メール" and domain == NAKABOSHI_DOMAIN:
        return COLOR_NAKABOSHI
    return COLOR_DEFAULT


def color_legend_html() -> str:
    return """
    <div class="mescheck-legend">
      <span><span class="dot" style="background:#1565C0"></span>社内ドメイン (nakaboshi.co.jp)</span>
      <span><span class="dot" style="background:#1B7A1B"></span>Teams 投稿</span>
      <span><span class="dot" style="background:#B8860B"></span>登録ドメイン（チェック済み）</span>
    </div>
    """


def loading_css() -> str:
    return """
    <style>
    .mescheck-legend {
        display: flex; flex-wrap: wrap; gap: 1.2rem;
        font-size: 0.85rem; margin-bottom: 0.5rem; color: #444;
    }
    .mescheck-legend .dot {
        display: inline-block; width: 10px; height: 10px;
        border-radius: 50%; margin-right: 6px; vertical-align: middle;
    }
    .mescheck-loading-title {
        font-size: 1.1rem; font-weight: 700;
        animation: mescheck-pulse 1.2s ease-in-out infinite;
    }
    @keyframes mescheck-pulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.55; transform: scale(1.02); }
    }
    .mescheck-row-text { line-height: 1.45; font-size: 0.92rem; }
    </style>
    """


def _sort_ts(msg: dict) -> float:
    if "_sort_ts" in msg:
        return float(msg["_sort_ts"])
    return 0.0


def _sender_key(msg: dict) -> str:
    email = (msg.get("発信者メール") or "").strip()
    name = (msg.get("発信者名") or "").strip()
    if email:
        return email.lower()
    return name or "（不明）"


def _classify_display_tier(msg: dict, highlighted_domains: set[str]) -> int:
    source = msg.get("種別", "")
    domain = (msg.get("_domain") or "").lower()

    if source == "Teams Team 投稿":
        return 1
    if source == "Teams チャット":
        return 2
    if source == "Outlook メール":
        if domain == NAKABOSHI_DOMAIN:
            return 3
        if domain and domain in highlighted_domains:
            return 4
        return 5
    return 5


def _sort_flat_newest(items: list[dict]) -> list[dict]:
    return sorted(items, key=_sort_ts, reverse=True)


def _sort_by_sender_newest(items: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for msg in items:
        groups[_sender_key(msg)].append(msg)

    grouped: list[list[dict]] = []
    for group in groups.values():
        group.sort(key=_sort_ts, reverse=True)
        grouped.append(group)

    grouped.sort(key=lambda g: _sort_ts(g[0]), reverse=True)

    result: list[dict] = []
    for group in grouped:
        result.extend(group)
    return result


def sort_messages_for_display(messages: list[dict], highlighted_domains: set[str]) -> list[dict]:
    """表示順: ①Team投稿 ②Teamsチャット(発信者毎) ③社内メール ④登録ドメイン ⑤その他"""
    buckets: dict[int, list[dict]] = {1: [], 2: [], 3: [], 4: [], 5: []}
    for msg in messages:
        tier = _classify_display_tier(msg, highlighted_domains)
        buckets[tier].append(msg)

    ordered: list[dict] = []
    ordered.extend(_sort_flat_newest(buckets[1]))
    ordered.extend(_sort_by_sender_newest(buckets[2]))
    ordered.extend(_sort_by_sender_newest(buckets[3]))
    ordered.extend(_sort_by_sender_newest(buckets[4]))
    ordered.extend(_sort_flat_newest(buckets[5]))
    return ordered


def list_unique_domains(messages: list[dict]) -> list[str]:
    domains = {normalize_domain(m.get("_domain", "")) for m in messages}
    domains.discard("")
    return sorted(domains)


def normalize_domain_filter(text_query: str, selected_domain: str) -> str:
    if selected_domain and selected_domain != "（すべて）":
        return selected_domain.strip().lower()
    return text_query.strip().lower()


def filter_messages_by_domain(messages: list[dict], domain_filter: str) -> list[dict]:
    if not domain_filter:
        return messages
    needle = domain_filter.lower()
    return [
        msg
        for msg in messages
        if needle in (msg.get("_domain") or "").lower()
        or needle in (msg.get("発信者メール") or "").lower()
    ]


def domains_in_messages(messages: list[dict]) -> set[str]:
    return {normalize_domain(m.get("_domain", "")) for m in messages if m.get("_domain")}


def filter_messages_by_source(
    messages: list[dict],
    *,
    show_teams_post: bool,
    show_teams_chat: bool,
    show_mail: bool,
) -> list[dict]:
    allowed: set[str] = set()
    if show_teams_post:
        allowed.add(SOURCE_TEAMS_POST)
    if show_teams_chat:
        allowed.add(SOURCE_TEAMS_CHAT)
    if show_mail:
        allowed.add(SOURCE_MAIL)
    return [msg for msg in messages if msg.get("種別") in allowed]

