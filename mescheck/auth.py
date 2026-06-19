import os

import msal
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    "User.Read",
    "Mail.Read",
    "Chat.Read",
    "Channel.ReadBasic.All",
    "ChannelMessage.Read.All",
    "Team.ReadBasic.All",
]


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"環境変数 {name} が設定されていません。.env.example を参照してください。")
    return value


def get_redirect_uri() -> str:
    return os.getenv("REDIRECT_URI", "http://172.16.16.10:8511/").strip()


def get_msal_app() -> msal.ConfidentialClientApplication:
    tenant_id = os.getenv("AZURE_TENANT_ID", "common").strip() or "common"
    return msal.ConfidentialClientApplication(
        client_id=_require_env("AZURE_CLIENT_ID"),
        client_credential=_require_env("AZURE_CLIENT_SECRET"),
        authority=f"https://login.microsoftonline.com/{tenant_id}",
    )


def build_auth_url(login_hint: str | None = None) -> str:
    app = get_msal_app()
    kwargs = {}
    if login_hint:
        kwargs["login_hint"] = login_hint
    return app.get_authorization_request_url(
        SCOPES,
        redirect_uri=get_redirect_uri(),
        prompt="select_account",
        **kwargs,
    )


def acquire_token_by_code(code: str) -> dict:
    app = get_msal_app()
    return app.acquire_token_by_authorization_code(
        code,
        scopes=SCOPES,
        redirect_uri=get_redirect_uri(),
    )


def init_auth_state() -> None:
    if "access_token" not in st.session_state:
        st.session_state.access_token = None
    if "account" not in st.session_state:
        st.session_state.account = None
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "last_fetched_at" not in st.session_state:
        st.session_state.last_fetched_at = None
    if "fetch_error" not in st.session_state:
        st.session_state.fetch_error = None
    if "fetch_warnings" not in st.session_state:
        st.session_state.fetch_warnings = []


def save_token_result(result: dict) -> bool:
    if "access_token" not in result:
        return False
    st.session_state.access_token = result["access_token"]
    st.session_state.account = result.get("id_token_claims", {})
    return True


def clear_auth() -> None:
    st.session_state.access_token = None
    st.session_state.account = None
    st.session_state.messages = []
    st.session_state.last_fetched_at = None
    st.session_state.fetch_error = None
    st.session_state.fetch_warnings = []


def get_access_token() -> str | None:
    return st.session_state.get("access_token")


def get_account_label() -> str:
    account = st.session_state.get("account") or {}
    return account.get("preferred_username") or account.get("name") or "（不明）"
