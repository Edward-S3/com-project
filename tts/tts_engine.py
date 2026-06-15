"""Google Gemini Text-to-Speech API ラッパー"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Callable
from dataclasses import dataclass

from dotenv import load_dotenv
from google import genai
from google.genai import types

NAI_ENV_PATH = "/opt/gemini-ui/.env"
LOCAL_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

TTS_MODELS: dict[str, str] = {
    "gemini-3.1-flash-tts-preview": "Gemini 3.1 Flash TTS（推奨・高速）",
    "gemini-2.5-flash-preview-tts": "Gemini 2.5 Flash TTS",
    "gemini-2.5-pro-preview-tts": "Gemini 2.5 Pro TTS（高品質）",
}

VOICE_OPTIONS: dict[str, str] = {
    "Zephyr": "Zephyr — 明るい",
    "Puck": "Puck — 快活",
    "Charon": "Charon — 情報的",
    "Kore": "Kore — 落ち着いた",
    "Fenrir": "Fenrir — 活発",
    "Leda": "Leda — 若々しい",
    "Orus": "Orus — 力強い",
    "Aoede": "Aoede — 軽やか",
    "Callirrhoe": "Callirrhoe — 気楽",
    "Autonoe": "Autonoe — 明るい",
    "Enceladus": "Enceladus — 息遣い",
    "Iapetus": "Iapetus — クリア",
    "Umbriel": "Umbriel — 気楽",
    "Algieba": "Algieba — 滑らか",
    "Despina": "Despina — 滑らか",
    "Erinome": "Erinome — クリア",
    "Algenib": "Algenib — 渋い",
    "Rasalgethi": "Rasalgethi — 情報的",
    "Laomedeia": "Laomedeia — 快活",
    "Achernar": "Achernar — 柔らか",
    "Alnilam": "Alnilam — 力強い",
    "Schedar": "Schedar — 均一",
    "Gacrux": "Gacrux — 成熟",
    "Pulcherrima": "Pulcherrima — 前向き",
    "Achird": "Achird — 親しみやすい",
    "Zubenelgenubi": "Zubenelgenubi — カジュアル",
    "Vindemiatrix": "Vindemiatrix — 優しい",
    "Sadachbia": "Sadachbia — 活気",
    "Sadaltager": "Sadaltager — 知的",
    "Sulafat": "Sulafat — 温かい",
}

SAMPLE_RATE = 24000
CHANNELS = 1
SAMPLE_WIDTH = 2

DOWNLOAD_FORMATS: dict[str, dict[str, str]] = {
    "wav": {"label": "WAV（無圧縮・高品質）", "ext": "wav", "mime": "audio/wav"},
    "mp3": {"label": "MP3（一般的・ファイル小）", "ext": "mp3", "mime": "audio/mpeg"},
    "m4a": {"label": "M4A（AAC・iPhone等と相性良）", "ext": "m4a", "mime": "audio/mp4"},
    "ogg": {"label": "OGG（オープン形式）", "ext": "ogg", "mime": "audio/ogg"},
}


class SynthesisCancelled(Exception):
    """ユーザーにより音声合成がキャンセルされた"""


@dataclass
class SynthesisResult:
    wav_bytes: bytes
    mime_type: str
    model: str
    voice: str


def load_api_key() -> str:
    """NAI（/opt/gemini-ui）と同一の GOOGLE_API_KEY を読み込む。"""
    load_dotenv(NAI_ENV_PATH)
    load_dotenv(LOCAL_ENV_PATH, override=True)
    key = os.getenv("GOOGLE_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "GOOGLE_API_KEY が見つかりません。"
            f" {NAI_ENV_PATH} または {LOCAL_ENV_PATH} を確認してください。"
        )
    return key


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def available_download_formats() -> dict[str, str]:
    """利用可能なダウンロード形式（WAV は常時、他は ffmpeg 要）"""
    formats = {"wav": DOWNLOAD_FORMATS["wav"]["label"]}
    if ffmpeg_available():
        formats["mp3"] = DOWNLOAD_FORMATS["mp3"]["label"]
        formats["m4a"] = DOWNLOAD_FORMATS["m4a"]["label"]
        formats["ogg"] = DOWNLOAD_FORMATS["ogg"]["label"]
    return formats


def convert_audio(wav_bytes: bytes, fmt: str) -> tuple[bytes, str, str]:
    """
    WAV バイト列を指定形式に変換。
    戻り値: (data, mime_type, extension)
    """
    fmt = (fmt or "wav").lower()
    if fmt not in DOWNLOAD_FORMATS:
        raise ValueError(f"未対応の形式です: {fmt}")
    if fmt == "wav":
        meta = DOWNLOAD_FORMATS["wav"]
        return wav_bytes, meta["mime"], meta["ext"]
    if not ffmpeg_available():
        raise RuntimeError(
            "MP3 / M4A / OGG への変換には ffmpeg が必要です。"
            " サーバー管理者にインストールを依頼するか、WAV をご利用ください。"
        )

    codec_map = {"mp3": "libmp3lame", "ogg": "libvorbis"}
    meta = DOWNLOAD_FORMATS[fmt]
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_f:
        wav_f.write(wav_bytes)
        wav_path = wav_f.name
    out_path = wav_path.rsplit(".", 1)[0] + f".{meta['ext']}"
    if fmt == "m4a":
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", wav_path,
            "-c:a", "aac", "-b:a", "192k",
            out_path,
        ]
    else:
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", wav_path,
            "-acodec", codec_map[fmt],
            out_path,
        ]
    try:
        proc = subprocess.run(
            ffmpeg_cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "変換に失敗しました").strip()
            raise RuntimeError(err)
        with open(out_path, "rb") as f:
            return f.read(), meta["mime"], meta["ext"]
    finally:
        for path in (wav_path, out_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def pcm_to_wav(pcm_data: bytes, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def _check_cancelled(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check and cancel_check():
        raise SynthesisCancelled()


def synthesize_speech(
    text: str,
    *,
    model: str,
    voice: str,
    style_prompt: str = "",
    cancel_check: Callable[[], bool] | None = None,
) -> SynthesisResult:
    text = (text or "").strip()
    if not text:
        raise ValueError("合成するテキストを入力してください。")
    if model not in TTS_MODELS:
        raise ValueError(f"未対応のモデルです: {model}")
    if voice not in VOICE_OPTIONS:
        raise ValueError(f"未対応の音声です: {voice}")

    _check_cancelled(cancel_check)

    prompt = text
    style = (style_prompt or "").strip()
    if style:
        prompt = f"{style}\n\n{text}"

    client = genai.Client(api_key=load_api_key())
    _check_cancelled(cancel_check)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice,
                    )
                )
            ),
        ),
    )
    _check_cancelled(cancel_check)

    candidates = response.candidates or []
    if not candidates or not candidates[0].content:
        feedback = getattr(response, "prompt_feedback", None)
        raise RuntimeError(f"音声合成に失敗しました。{feedback or ''}")

    parts = candidates[0].content.parts or []
    audio_part = next((p for p in parts if p.inline_data and p.inline_data.data), None)
    if not audio_part:
        raise RuntimeError("API から音声データが返されませんでした。")

    pcm = audio_part.inline_data.data
    wav_bytes = pcm_to_wav(pcm)
    return SynthesisResult(
        wav_bytes=wav_bytes,
        mime_type="audio/wav",
        model=model,
        voice=voice,
    )
