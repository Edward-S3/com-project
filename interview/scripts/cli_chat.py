#!/usr/bin/env python3
"""CLI for one-turn text dialogue and forbidden-expression checking (P1)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.checker import check_text, format_warnings
from app.config import ENV_PATH, load_settings, resolve_role_provider
from app.mode_prompts import VALID_MODES, get_mode_description, get_system_prompt
from app.providers.base import Message
from app.providers.registry import get_provider_for_role


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="1on1 simulation CLI (P1)")
    parser.add_argument(
        "--mode",
        choices=sorted(VALID_MODES),
        default="1A",
        help="Simulation mode identifier",
    )
    parser.add_argument(
        "--message",
        "-m",
        help="User message. If omitted, reads from stdin.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings()
    provider_name = resolve_role_provider(settings, "partner")
    use_mock = not ENV_PATH.exists()
    provider = get_provider_for_role(settings, "partner", allow_mock=True)

    user_text = args.message
    if not user_text:
        print("発言を入力してください: ", end="", flush=True)
        user_text = sys.stdin.readline().strip()

    if not user_text:
        print("エラー: 発言が空です。", file=sys.stderr)
        return 1

    print(f"モード: {args.mode} ({get_mode_description(args.mode)})")
    print(f"プロバイダ: {provider.name} (設定値: {provider_name})")
    if use_mock:
        print("注意: .env がないためモックプロバイダを使用しています。")

    matches = check_text(user_text)
    warnings = format_warnings(matches)
    if warnings:
        print("\n[禁止表現警告]")
        for warning in warnings:
            print(f"  - {warning}")

    system_prompt = get_system_prompt(args.mode)
    messages = [Message(role="user", content=user_text)]
    response = provider.generate(system_prompt, messages)

    print("\n[AI応答]")
    print(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
