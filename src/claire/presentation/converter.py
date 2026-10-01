"""Asciidoctor reveal.js compiler process wrapper.

Executes asciidoctor with revealjs backend using stdin pipe, concurrency limiting,
timeouts, and in-flight request deduplication.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path


_SEMAPHORE = asyncio.Semaphore(2)
_IN_FLIGHT: dict[str, asyncio.Future[Path]] = {}
_IN_FLIGHT_LOCK = asyncio.Lock()


def find_asciidoctor_executable() -> list[str] | None:
    """Find available asciidoctor executable with revealjs backend."""
    # 1. Direct asciidoctor-revealjs CLI
    cli = shutil.which("asciidoctor-revealjs")
    if cli:
        return [cli]

    # 2. General asciidoctor with gem
    asc = shutil.which("asciidoctor")
    if asc:
        return [asc, "-r", "asciidoctor-revealjs", "-b", "revealjs"]

    # 3. Known Homebrew / Linuxbrew / host paths
    candidates = [
        "/home/linuxbrew/.linuxbrew/bin/asciidoctor",
        "/usr/local/bin/asciidoctor",
        "/usr/bin/asciidoctor",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return [c, "-r", "asciidoctor-revealjs", "-b", "revealjs"]

    return None


async def compile_presentation_html(
    adoc_content: str,
    output_path: Path,
    *,
    timeout_sec: float = 30.0,
    doc_id: str | None = None,
) -> tuple[Path, int]:
    """Compile AsciiDoc string to reveal.js HTML presentation file.

    Returns:
        (output_path, compile_duration_ms)
    """
    key = doc_id or str(output_path)

    # In-flight deduplication
    async with _IN_FLIGHT_LOCK:
        if key in _IN_FLIGHT:
            future = _IN_FLIGHT[key]
            # Wait for the in-flight compilation to complete
            path = await future
            return path, 0
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        _IN_FLIGHT[key] = future

    try:
        duration_ms = await _do_compile(adoc_content, output_path, timeout_sec=timeout_sec)
        future.set_result(output_path)
        return output_path, duration_ms
    except Exception as exc:
        future.set_exception(exc)
        raise
    finally:
        async with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.pop(key, None)


async def _do_compile(
    adoc_content: str,
    output_path: Path,
    *,
    timeout_sec: float = 30.0,
) -> int:
    cmd_base = find_asciidoctor_executable()
    if not cmd_base:
        raise RuntimeError("asciidoctor-revealjs executable not found in system")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.with_suffix(".tmp")

    cmd = list(cmd_base) + ["-o", str(temp_output), "-"]

    async with _SEMAPHORE:
        start_time = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=adoc_content.encode("utf-8")),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            if temp_output.exists():
                temp_output.unlink(missing_ok=True)
            raise TimeoutError(f"asciidoctor compilation timed out after {timeout_sec}s")

        duration_ms = int((time.monotonic() - start_time) * 1000)

        if proc.returncode != 0:
            if temp_output.exists():
                temp_output.unlink(missing_ok=True)
            err_msg = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"asciidoctor compilation failed (exit {proc.returncode}): {err_msg}")

        # Inject Claire Bible HUD toolbar into generated HTML
        from .hud import inject_hud_toolbar
        try:
            raw_html = temp_output.read_text(encoding="utf-8")
            injected = inject_hud_toolbar(raw_html)
            temp_output.write_text(injected, encoding="utf-8")
        except Exception:
            pass

        # Atomic move to final output path
        temp_output.replace(output_path)
        return duration_ms
