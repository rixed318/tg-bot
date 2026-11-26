"""Unified headless LLM CLI runner supporting Gemini (agy-proxy) and Grok CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

from tg_bot.config import AGY_BIN, GROK_BIN

_META_PREAMBLE_RE = re.compile(
    r"^(?:"
    r"(?:сначала|сейчас|щас|давай|пойду|надо|нужно|попробую|могу|давайте)\s+"
    r"(?:гляну|посмотрю|проверю|открою|прочитаю|изучу|поищу|чекну)\s+"
    r"(?:историю|контекст|чат|файл|сообщения|переписку|full-context)[^.!?\n]*[.!?\n]+"
    r"|(?:i['’]?ll|let me|first,? i['’]?ll)\s+(?:check|read|look at|inspect)\s+[^.!?\n]*[.!?\n]+"
    r")\s*",
    flags=re.IGNORECASE,
)


def strip_meta_thoughts(text: str) -> str:
    """Strip accidental model pre-tool 'thinking aloud' preambles before the spoken reply."""
    cleaned = text.strip()
    for _ in range(3):
        updated = _META_PREAMBLE_RE.sub("", cleaned, count=1).strip()
        if updated == cleaned:
            break
        cleaned = updated
    return cleaned


def _extract_grok_response(stdout: str) -> str:
    """Parse JSON or streaming-json output from grok CLI."""
    text = stdout.strip()
    if not text:
        return ""
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if data.get("type") == "error" or data.get("error"):
                raise RuntimeError(str(data.get("message") or data.get("error")))
            for key in ("response", "result", "text", "content", "output_text"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    return strip_meta_thoughts(val)
    except json.JSONDecodeError:
        pass

    # Fallback: scan NDJSON lines from bottom
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            if obj.get("type") == "error":
                raise RuntimeError(str(obj.get("message") or "grok error"))
            for key in ("response", "result", "text", "content", "output_text"):
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    return strip_meta_thoughts(val)
    return strip_meta_thoughts(text)


def run_llm_prompt(
    prompt_text: str,
    *,
    work_dir: Path,
    model_id: str,
    timeout_seconds: int = 150,
) -> str:
    """Execute a headless single-turn prompt via Gemini (agy-proxy) or Grok CLI."""
    if model_id.startswith("grok"):
        command = [
            str(GROK_BIN),
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "json",
            "-m",
            model_id,
            "-p",
            prompt_text,
        ]
        result = subprocess.run(
            command,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 30,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"grok failed ({result.returncode}): {err[-400:]}")
        return _extract_grok_response(result.stdout)

    command = [
        str(AGY_BIN),
        "--dangerously-skip-permissions",
        "--model",
        model_id,
        "--output-format",
        "json",
        "--print-timeout",
        f"{timeout_seconds}s",
        f"--print={prompt_text}",
    ]
    result = subprocess.run(
        command,
        cwd=work_dir,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds + 30,
    )
    payload = json.loads(result.stdout)
    if payload.get("status") != "SUCCESS":
        raise RuntimeError(f"agy returned {payload.get('status')}")
    return strip_meta_thoughts(str(payload.get("response") or ""))
