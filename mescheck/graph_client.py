from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import requests

from domain_store import extract_domain
from summarizer import summarize

ProgressCallback = Callable[[str, float, str], None]

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


@dataclass
class MessageItem:
    source: str
    received_at: datetime
    sender_name: str
    sender_email: str
    title: str
    summary: str
    raw_id: str

    def to_row(self) -> dict[str, str]:
        domain = extract_domain(self.sender_email)
        return {
            "種別": self.source,
            "受信日時": self.received_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            "発信者名": self.sender_name,
            "発信者メール": self.sender_email,
            "タイトル": self.title,
            "本文要約": self.summary,
            "_raw_id": self.raw_id or f"{self.source}:{self.received_at.isoformat()}",
            "_domain": domain,
            "_sort_ts": self.received_at.timestamp(),
        }


class GraphClient:
    def __init__(self, access_token: str):
        self._user_id: str | None = None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
        )

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
        response = self.session.get(url, params=params, timeout=60)
        response.raise_for_status()
        return response.json()

    def _get_all(self, path: str, params: dict | None = None, max_pages: int = 5) -> list[dict]:
        items: list[dict] = []
        next_url: str | None = path if path.startswith("http") else f"{GRAPH_BASE}{path}"
        next_params = params
        pages = 0
        while next_url and pages < max_pages:
            data = self._get(next_url, params=next_params if not next_url.startswith("http") else None)
            if not next_url.startswith("http"):
                next_params = None
            items.extend(data.get("value", []))
            next_url = data.get("@odata.nextLink")
            pages += 1
        return items

    def fetch_all_messages(
        self,
        on_progress: ProgressCallback | None = None,
    ) -> tuple[list[MessageItem], list[str]]:
        def report(label: str, ratio: float, detail: str) -> None:
            if on_progress:
                on_progress(label, ratio, detail)

        report("準備中", 0.02, "Microsoft Graph API への接続を開始します…")
        me = self._get("/me")
        self._user_id = me.get("id")
        report("準備中", 0.08, f"ユーザー情報を取得しました（{me.get('displayName', '不明')}）")

        items: list[MessageItem] = []
        warnings: list[str] = []

        fetch_steps = (
            ("Outlook メール", 0.10, 0.40, self._fetch_unread_emails),
            ("Teams チャット", 0.40, 0.70, self._fetch_unread_chat_messages),
            ("Teams Team 投稿", 0.70, 0.95, self._fetch_team_channel_messages),
        )
        for label, start_ratio, end_ratio, fetcher in fetch_steps:
            report(label, start_ratio, f"{label} を取得しています…")
            try:
                fetched = fetcher()
                items.extend(fetched)
                report(label, end_ratio, f"{label} を {len(fetched)} 件取得しました")
            except requests.HTTPError as exc:
                warnings.append(f"{label}: {_http_error_detail(exc)}")
                report(label, end_ratio, f"{label} の取得に失敗しました（他のデータは続行します）")

        items.sort(key=lambda item: item.received_at, reverse=True)
        report("完了", 1.0, f"合計 {len(items)} 件のメッセージを取得しました")
        return items, warnings

    def _fetch_unread_emails(self) -> list[MessageItem]:
        rows: list[MessageItem] = []
        messages = self._get_all(
            "/me/mailFolders/inbox/messages",
            params={
                "$filter": "isRead eq false",
                # body は取得しない（GET のみ・未読状態を変えないため bodyPreview のみ使用）
                "$select": "id,receivedDateTime,from,subject,bodyPreview",
                "$orderby": "receivedDateTime desc",
                "$top": "50",
            },
            max_pages=2,
        )
        for message in messages:
            sender = (message.get("from") or {}).get("emailAddress") or {}
            body = message.get("bodyPreview") or ""
            rows.append(
                MessageItem(
                    source="Outlook メール",
                    received_at=_parse_dt(message.get("receivedDateTime")),
                    sender_name=sender.get("name") or "（不明）",
                    sender_email=sender.get("address") or "",
                    title=message.get("subject") or "（件名なし）",
                    summary=summarize(body),
                    raw_id=message.get("id") or "",
                )
            )
        return rows

    def _fetch_unread_chat_messages(self) -> list[MessageItem]:
        rows: list[MessageItem] = []
        chats = self._get_all(
            "/me/chats",
            params={
                "$expand": "members",
                "$select": "id,topic,chatType,viewpoint",
                "$top": "50",
            },
            max_pages=2,
        )
        for chat in chats:
            chat_id = chat.get("id")
            if not chat_id:
                continue
            last_read = (chat.get("viewpoint") or {}).get("lastMessageReadDateTime")
            last_read_dt = _parse_dt(last_read) if last_read else None
            title = _chat_title(chat)
            messages = self._get_all(
                f"/me/chats/{chat_id}/messages",
                params={
                    "$top": "20",
                    "$orderby": "createdDateTime desc",
                },
                max_pages=1,
            )
            for message in messages:
                if message.get("messageType") != "message":
                    continue
                created = _parse_dt(message.get("createdDateTime"))
                if last_read_dt and created <= last_read_dt:
                    continue
                sender = message.get("from") or {}
                user = sender.get("user") or {}
                app = sender.get("application") or {}
                sender_name = user.get("displayName") or app.get("displayName") or "（不明）"
                sender_email = user.get("userIdentityType", "")
                if user.get("id"):
                    sender_email = ""
                body = _chat_body(message)
                rows.append(
                    MessageItem(
                        source="Teams チャット",
                        received_at=created,
                        sender_name=sender_name,
                        sender_email=sender_email,
                        title=title,
                        summary=summarize(body),
                        raw_id=message.get("id") or "",
                    )
                )
        return rows

    def _fetch_team_channel_messages(self) -> list[MessageItem]:
        rows: list[MessageItem] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        teams = self._get_all("/me/joinedTeams", params={"$select": "id,displayName"}, max_pages=2)
        for team in teams:
            team_id = team.get("id")
            team_name = team.get("displayName") or "（Team 名不明）"
            if not team_id:
                continue
            try:
                channels = self._get_all(
                    f"/teams/{team_id}/channels",
                    params={"$select": "id,displayName"},
                    max_pages=1,
                )
            except requests.HTTPError:
                continue
            for channel in channels:
                channel_id = channel.get("id")
                if not channel_id:
                    continue
                channel_name = channel.get("displayName") or "general"
                try:
                    messages = self._get_all(
                        f"/teams/{team_id}/channels/{channel_id}/messages",
                        params={"$top": "20"},
                        max_pages=1,
                    )
                except requests.HTTPError:
                    continue
                for message in messages:
                    if message.get("messageType") != "message":
                        continue
                    created = _parse_dt(message.get("createdDateTime"))
                    if created < cutoff:
                        continue
                    sender = message.get("from") or {}
                    user = sender.get("user") or {}
                    if user.get("id") and user.get("id") == getattr(self, "_user_id", None):
                        continue
                    user = sender.get("user") or {}
                    app = sender.get("application") or {}
                    sender_name = user.get("displayName") or app.get("displayName") or "（不明）"
                    body = _chat_body(message)
                    rows.append(
                        MessageItem(
                            source="Teams Team 投稿",
                            received_at=created,
                            sender_name=sender_name,
                            sender_email="",
                            title=f"{team_name} / {channel_name}",
                            summary=summarize(body),
                            raw_id=message.get("id") or "",
                        )
                    )
        return rows


def _http_error_detail(exc: requests.HTTPError) -> str:
    response = exc.response
    if response is None:
        return str(exc)
    try:
        payload = response.json()
        err = payload.get("error") or {}
        message = err.get("message") or response.text
        return f"{response.status_code} {message}"
    except ValueError:
        return f"{response.status_code} {response.reason} for url: {response.url}"


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    # Graph API は小数秒の桁数が可変（例: .64, .640, .6400000）のため正規化する
    match = re.match(
        r"^(?P<head>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d+))?(?P<tz>[+-]\d{2}:\d{2})$",
        value,
    )
    if match:
        head = match.group("head")
        frac = match.group("frac")
        tz = match.group("tz")
        if frac is not None:
            value = f"{head}.{(frac + '000000')[:6]}{tz}"
        else:
            value = f"{head}{tz}"

    return datetime.fromisoformat(value)


def _chat_title(chat: dict[str, Any]) -> str:
    topic = (chat.get("topic") or "").strip()
    if topic:
        return topic
    chat_type = chat.get("chatType") or ""
    if chat_type == "oneOnOne":
        return "1:1 チャット"
    if chat_type == "group":
        return "グループチャット"
    return "Teams チャット"


def _chat_body(message: dict[str, Any]) -> str:
    body = message.get("body") or {}
    content = body.get("content") or ""
    if content:
        return content
    return message.get("summary") or ""
