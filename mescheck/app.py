import html
from datetime import timedelta

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from auth import (
    acquire_token_by_code,
    build_auth_url,
    clear_auth,
    get_access_token,
    get_account_label,
    init_auth_state,
    save_token_result,
)
from display import (
    color_legend_html,
    domains_in_messages,
    filter_messages_by_domain,
    filter_messages_by_source,
    list_unique_domains,
    loading_css,
    normalize_domain_filter,
    resolve_row_color,
    sort_messages_for_display,
)
from domain_store import (
    add_domain,
    init_db,
    is_valid_domain,
    list_domains,
    list_domains_sorted,
    parse_manual_domain,
    remove_domain,
    set_domains,
)
from graph_client import GraphClient
from user_settings import (
    MIN_REFRESH_MINUTES,
    UserSettings,
    get_user_settings,
    init_user_settings_db,
    save_user_settings,
)

load_dotenv()
init_db()
init_user_settings_db()

st.set_page_config(
    page_title="メッセージ確認 (MesCheck)",
    page_icon="📬",
    layout="wide",
)

st.markdown(loading_css(), unsafe_allow_html=True)


def _handle_oauth_callback() -> None:
    params = st.query_params
    if "code" in params and not get_access_token():
        result = acquire_token_by_code(params["code"])
        if save_token_result(result):
            st.query_params.clear()
            st.rerun()
        else:
            error = result.get("error_description") or result.get("error") or "認証に失敗しました。"
            st.error(error)


def _login_view() -> None:
    st.title("📬 メッセージ確認 (MesCheck)")
    st.markdown(
        "Microsoft 365 アカウントでサインインし、未読の Outlook メールと "
        "Teams チャット / Team 投稿を一覧表示します。"
    )

    email_hint = st.text_input(
        "Microsoft 365 メールアドレス",
        placeholder="user@example.com",
        help="サインイン画面で使用するアカウントのヒントとして利用します（任意）。",
    )

    try:
        auth_url = build_auth_url(login_hint=email_hint.strip() or None)
        st.link_button(
            "Microsoft でサインイン",
            auth_url,
            type="primary",
            use_container_width=True,
        )
    except RuntimeError as exc:
        st.error(str(exc))

    st.divider()
    st.caption(
        "初回利用前に Azure AD（Microsoft Entra ID）でアプリ登録し、"
        "`.env` に CLIENT_ID / CLIENT_SECRET / TENANT_ID を設定してください。"
    )


def _make_domain_toggle_handler(raw_id: str):
    def _handler() -> None:
        domain = st.session_state.row_domain_map.get(raw_id, "")
        if not domain:
            return
        user_email = get_account_label().strip().lower()
        checked = st.session_state.get(f"hl_{raw_id}", False)
        if checked:
            add_domain(user_email, domain)
        else:
            remove_domain(user_email, domain)
        for rid, dom in st.session_state.row_domain_map.items():
            if dom == domain:
                st.session_state[f"hl_{rid}"] = checked

    return _handler


def _sync_checkbox_states_from_db(messages: list[dict]) -> None:
    user_email = get_account_label().strip().lower()
    highlighted = list_domains(user_email)
    for msg in messages:
        domain = msg.get("_domain", "")
        if domain:
            st.session_state[f"hl_{msg['_raw_id']}"] = domain in highlighted


def _fetch_messages(show_progress: bool = True) -> None:
    token = get_access_token()
    if not token:
        return

    progress_bar = None
    status_area = None
    detail_area = None
    loading_title = None

    if show_progress:
        loading_title = st.empty()
        loading_title.markdown(
            '<p class="mescheck-loading-title">📡 メッセージを読み込んでいます…</p>',
            unsafe_allow_html=True,
        )
        progress_bar = st.progress(0, text="0% — 準備中…")
        status_area = st.empty()
        detail_area = st.empty()

    def on_progress(label: str, ratio: float, detail: str) -> None:
        pct = int(min(max(ratio, 0.0), 1.0) * 100)
        if progress_bar is not None:
            progress_bar.progress(min(max(ratio, 0.0), 1.0), text=f"{pct}% — {label}")
        if status_area is not None:
            status_area.markdown(f"### 🔄 現在の処理: **{label}**")
        if detail_area is not None:
            detail_area.info(detail)

    try:
        client = GraphClient(token)
        items, warnings = client.fetch_all_messages(on_progress=on_progress)
        messages = [item.to_row() for item in items]
        st.session_state.messages = messages
        st.session_state.row_domain_map = {
            msg["_raw_id"]: msg.get("_domain", "") for msg in messages
        }
        _sync_checkbox_states_from_db(messages)
        st.session_state.last_fetched_at = pd.Timestamp.now(tz="Asia/Tokyo")
        st.session_state.fetch_warnings = warnings
        st.session_state.fetch_error = None
    except Exception as exc:
        st.session_state.fetch_error = str(exc)
        st.session_state.fetch_warnings = []
    finally:
        if loading_title is not None:
            loading_title.empty()
        if progress_bar is not None:
            progress_bar.empty()
        if status_area is not None:
            status_area.empty()
        if detail_area is not None:
            detail_area.empty()


def _minutes_since_last_fetch() -> float | None:
    last = st.session_state.get("last_fetched_at")
    if last is None:
        return None
    now = pd.Timestamp.now(tz="Asia/Tokyo")
    return (now - last).total_seconds() / 60.0


def _render_auto_refresh_settings(user_email: str) -> UserSettings:
    prefs = get_user_settings(user_email)

    if st.session_state.get("settings_initialized_for") != user_email:
        st.session_state.settings_auto_refresh_enabled = prefs.auto_refresh_enabled
        st.session_state.settings_refresh_interval = prefs.refresh_interval_minutes
        st.session_state.settings_initialized_for = user_email

    with st.sidebar:
        st.subheader("⚙️ 自動更新設定")
        st.caption("設定はユーザーごとにサーバーへ保存されます。")

        enabled = st.toggle(
            "自動更新を有効にする",
            key="settings_auto_refresh_enabled",
            help="ブラウザを開いたままにした場合、一定間隔でメッセージを再取得します。",
        )
        interval = st.number_input(
            f"更新間隔（分・最低 {MIN_REFRESH_MINUTES} 分）",
            min_value=MIN_REFRESH_MINUTES,
            max_value=1440,
            step=1,
            key="settings_refresh_interval",
        )

        if st.button("設定を保存", type="primary", use_container_width=True):
            current = get_user_settings(user_email)
            saved = save_user_settings(
                user_email,
                UserSettings(
                    auto_refresh_enabled=enabled,
                    refresh_interval_minutes=int(interval),
                    show_teams_post=current.show_teams_post,
                    show_teams_chat=current.show_teams_chat,
                    show_mail=current.show_mail,
                ),
            )
            st.session_state.user_settings = saved
            st.success(f"保存しました（{saved.refresh_interval_minutes} 分間隔）")
            st.rerun()

        saved_prefs = get_user_settings(user_email)
        if saved_prefs.auto_refresh_enabled and st.session_state.get("last_fetched_at"):
            elapsed = _minutes_since_last_fetch()
            if elapsed is not None:
                remaining = max(0.0, saved_prefs.refresh_interval_minutes - elapsed)
                st.info(f"次の自動更新まで 約 **{int(remaining)}** 分")

    return get_user_settings(user_email)


@st.fragment(run_every=timedelta(seconds=30))
def _auto_refresh_watchdog() -> None:
    if not get_access_token():
        return

    user_email = get_account_label().strip().lower()
    prefs = get_user_settings(user_email)
    if not prefs.auto_refresh_enabled:
        return
    if st.session_state.get("last_fetched_at") is None:
        return

    elapsed = _minutes_since_last_fetch()
    if elapsed is None or elapsed < prefs.refresh_interval_minutes:
        return

    _fetch_messages(show_progress=True)
    st.rerun()


def _render_message_row(msg: dict, highlighted_domains: set[str]) -> None:
    raw_id = msg["_raw_id"]
    domain = msg.get("_domain", "")
    color = resolve_row_color(msg["種別"], msg.get("発信者メール", ""), highlighted_domains)

    cols = st.columns([0.04, 0.10, 0.14, 0.12, 0.14, 0.18, 0.28])
    with cols[0]:
        if domain:
            checkbox_key = f"hl_{raw_id}"
            if checkbox_key not in st.session_state:
                st.session_state[checkbox_key] = domain in highlighted_domains
            st.checkbox(
                "",
                key=checkbox_key,
                on_change=_make_domain_toggle_handler(raw_id),
                label_visibility="collapsed",
            )
        else:
            st.checkbox(
                "",
                value=False,
                disabled=True,
                key=f"hl_disabled_{raw_id}",
                label_visibility="collapsed",
            )

    fields = [
        msg["種別"],
        msg["受信日時"],
        msg["発信者名"],
        msg["発信者メール"],
        msg["タイトル"],
        msg["本文要約"],
    ]
    for col, text in zip(cols[1:], fields):
        with col:
            safe = html.escape(str(text))
            st.markdown(
                f'<div class="mescheck-row-text" style="color:{color};">{safe}</div>',
                unsafe_allow_html=True,
            )


def _sync_checkbox_states_for_domains(domains: set[str], checked: bool) -> None:
    for rid, dom in st.session_state.row_domain_map.items():
        if dom in domains:
            st.session_state[f"hl_{rid}"] = checked


def _render_domain_filter_bar(sorted_messages: list[dict], user_email: str) -> list[dict]:
    unique_domains = list_unique_domains(sorted_messages)
    filter_col1, filter_col2, filter_col3 = st.columns([3, 2, 1])

    with filter_col1:
        st.text_input(
            "ドメイン検索・絞り込み",
            key="domain_filter_text",
            placeholder="example.com など（部分一致）",
            help="発信者メールのドメインで絞り込みます。空欄ですべて表示。",
        )
    with filter_col2:
        st.selectbox(
            "ドメイン一覧から選択",
            ["（すべて）"] + unique_domains,
            key="domain_filter_pick",
            help="一覧から選ぶと完全一致で絞り込みます。",
        )
    with filter_col3:
        if st.button("フィルター解除", use_container_width=True):
            st.session_state.domain_filter_text = ""
            st.session_state.domain_filter_pick = "（すべて）"
            st.rerun()

    effective_filter = normalize_domain_filter(
        st.session_state.get("domain_filter_text", ""),
        st.session_state.get("domain_filter_pick", "（すべて）"),
    )
    filtered = filter_messages_by_domain(sorted_messages, effective_filter)

    filter_domains = domains_in_messages(filtered)
    action_col1, action_col2, action_col3 = st.columns([1, 1, 2])
    with action_col1:
        if st.button(
            "表示中ドメインを登録",
            use_container_width=True,
            disabled=not filter_domains,
        ):
            set_domains(user_email, filter_domains, enabled=True)
            _sync_checkbox_states_for_domains(filter_domains, True)
            st.rerun()
    with action_col2:
        if st.button(
            "表示中ドメイン登録解除",
            use_container_width=True,
            disabled=not filter_domains,
        ):
            set_domains(user_email, filter_domains, enabled=False)
            _sync_checkbox_states_for_domains(filter_domains, False)
            st.rerun()
    with action_col3:
        if effective_filter:
            st.caption(
                f"🔍 フィルター: `{effective_filter}` — "
                f"**{len(filtered)}** 件 / 全 {len(sorted_messages)} 件"
            )
        else:
            st.caption(f"全 **{len(sorted_messages)}** 件中 **{len(filtered)}** 件を表示中")

    return filtered


def _render_manual_domain_registration(user_email: str) -> None:
    highlighted = list_domains(user_email)

    with st.expander("📌 チェック対象ドメインの手動登録", expanded=False):
        st.caption(
            "メール一覧に表示されていなくても、ドメインを登録しておくと "
            "該当メールが黄色で表示されます。"
        )
        reg_col1, reg_col2 = st.columns([5, 1])
        with reg_col1:
            st.text_input(
                "ドメインを入力",
                key="manual_domain_input",
                placeholder="example.com または user@example.com",
                label_visibility="collapsed",
            )
        with reg_col2:
            if st.button("登録", key="manual_domain_add", type="primary", use_container_width=True):
                raw = st.session_state.get("manual_domain_input", "")
                domain = parse_manual_domain(raw)
                if not is_valid_domain(domain):
                    st.error("有効なドメインを入力してください（例: example.com）")
                elif domain in highlighted:
                    st.warning(f"`{domain}` は既に登録されています。")
                else:
                    add_domain(user_email, domain)
                    _sync_checkbox_states_for_domains({domain}, True)
                    st.session_state.manual_domain_input = ""
                    st.success(f"`{domain}` を登録しました。")
                    st.rerun()

        registered = list_domains_sorted(user_email)
        if not registered:
            st.info("登録済みドメインはありません。")
            return

        st.markdown("**登録済みドメイン**")
        for domain in registered:
            row_col1, row_col2 = st.columns([5, 1])
            with row_col1:
                st.markdown(f"- `{domain}`")
            with row_col2:
                safe_key = domain.replace(".", "_")
                if st.button("削除", key=f"manual_domain_rm_{safe_key}", use_container_width=True):
                    remove_domain(user_email, domain)
                    _sync_checkbox_states_for_domains({domain}, False)
                    st.rerun()


def _persist_source_type_filters(user_email: str) -> None:
    current = get_user_settings(user_email)
    save_user_settings(
        user_email,
        UserSettings(
            auto_refresh_enabled=current.auto_refresh_enabled,
            refresh_interval_minutes=current.refresh_interval_minutes,
            show_teams_post=st.session_state.filter_teams_post,
            show_teams_chat=st.session_state.filter_teams_chat,
            show_mail=st.session_state.filter_mail,
        ),
    )


def _make_source_filter_handler(user_email: str, field_key: str):
    def _handler() -> None:
        if not (
            st.session_state.filter_teams_post
            or st.session_state.filter_teams_chat
            or st.session_state.filter_mail
        ):
            st.session_state[field_key] = True
            st.toast("種別は1つ以上選択してください。")
            return
        _persist_source_type_filters(user_email)

    return _handler


def _render_source_type_filter(user_email: str) -> tuple[bool, bool, bool]:
    prefs = get_user_settings(user_email)
    if st.session_state.get("source_filter_initialized_for") != user_email:
        st.session_state.filter_teams_post = prefs.show_teams_post
        st.session_state.filter_teams_chat = prefs.show_teams_chat
        st.session_state.filter_mail = prefs.show_mail
        st.session_state.source_filter_initialized_for = user_email

    st.markdown("**表示する種別**")
    st.caption("チェック状態はユーザーごとにサーバーへ保存されます。")
    type_col1, type_col2, type_col3 = st.columns(3)
    with type_col1:
        show_teams_post = st.checkbox(
            "Teams 投稿",
            key="filter_teams_post",
            on_change=_make_source_filter_handler(user_email, "filter_teams_post"),
        )
    with type_col2:
        show_teams_chat = st.checkbox(
            "Teams チャット",
            key="filter_teams_chat",
            on_change=_make_source_filter_handler(user_email, "filter_teams_chat"),
        )
    with type_col3:
        show_mail = st.checkbox(
            "メール",
            key="filter_mail",
            on_change=_make_source_filter_handler(user_email, "filter_mail"),
        )
    return show_teams_post, show_teams_chat, show_mail


def _render_message_table(messages: list[dict], highlighted_domains: set[str]) -> None:
    st.markdown(color_legend_html(), unsafe_allow_html=True)

    header = st.columns([0.04, 0.10, 0.14, 0.12, 0.14, 0.18, 0.28])
    labels = ["", "種別", "受信日時", "発信者名", "発信者メール", "タイトル", "本文要約"]
    for col, label in zip(header, labels):
        with col:
            st.markdown(f"**{label}**")

    st.divider()

    for msg in messages:
        _render_message_row(msg, highlighted_domains)


def _main_view() -> None:
    user_email = get_account_label().strip().lower()
    _render_auto_refresh_settings(user_email)
    _auto_refresh_watchdog()

    st.title("📬 メッセージ確認 (MesCheck)")

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        st.success(f"サインイン中: **{get_account_label()}**")
    with col2:
        if st.button("🔄 更新", type="primary", use_container_width=True):
            _fetch_messages(show_progress=True)
            st.rerun()
    with col3:
        if st.button("サインアウト", use_container_width=True):
            clear_auth()
            st.session_state.pop("settings_initialized_for", None)
            st.session_state.pop("source_filter_initialized_for", None)
            st.rerun()

    if st.session_state.fetch_error:
        st.error(f"データ取得エラー: {st.session_state.fetch_error}")

    for warning in st.session_state.get("fetch_warnings") or []:
        st.warning(f"一部のデータを取得できませんでした: {warning}")

    if st.session_state.last_fetched_at:
        prefs = get_user_settings(user_email)
        auto_label = (
            f" / 自動更新: **ON**（{prefs.refresh_interval_minutes} 分間隔）"
            if prefs.auto_refresh_enabled
            else " / 自動更新: **OFF**"
        )
        st.caption(
            f"最終更新: {st.session_state.last_fetched_at.strftime('%Y-%m-%d %H:%M:%S')}{auto_label}"
        )

    messages = st.session_state.messages
    if not messages:
        st.info("「更新」ボタンを押すと未読メール・Teams メッセージを取得します。")
        return

    highlighted_domains = list_domains(user_email)
    _render_manual_domain_registration(user_email)
    sorted_messages = sort_messages_for_display(messages, highlighted_domains)

    show_teams_post, show_teams_chat, show_mail = _render_source_type_filter(user_email)
    if not (show_teams_post or show_teams_chat or show_mail):
        st.warning("表示する種別を1つ以上選択してください。")
        return

    type_filtered = filter_messages_by_source(
        sorted_messages,
        show_teams_post=show_teams_post,
        show_teams_chat=show_teams_chat,
        show_mail=show_mail,
    )
    if not type_filtered:
        st.warning("選択した種別に該当するメッセージがありません。")
        return

    filtered_messages = _render_domain_filter_bar(type_filtered, user_email)
    if not filtered_messages:
        st.warning("条件に一致するメッセージがありません。フィルターを変更してください。")
        return

    _render_message_table(filtered_messages, highlighted_domains)
    st.caption(f"表示件数: {len(filtered_messages)} 件")


def main() -> None:
    init_auth_state()
    if "row_domain_map" not in st.session_state:
        st.session_state.row_domain_map = {}

    _handle_oauth_callback()

    if get_access_token():
        if st.session_state.last_fetched_at is None and not st.session_state.fetch_error:
            _fetch_messages(show_progress=True)
        _main_view()
    else:
        _login_view()


if __name__ == "__main__":
    main()
