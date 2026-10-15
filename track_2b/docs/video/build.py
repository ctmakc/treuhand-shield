#!/usr/bin/env python3
"""Build the <=2 min demo video from the real `make run` transcript.

Slides are rendered from track_2b/docs/results/make_run_transcript.txt (no mock-ups),
voice-over via edge-tts (en-US-AndrewNeural) or, if that service refuses, gTTS; ffmpeg.
Output: track_2b/docs/video/out/treuhand_shield_demo.mp4 (+ .srt)

Usage: python3 track_2b/docs/video/build.py
Needs: playwright (chromium), ffmpeg/ffprobe, edge-tts at EDGE_TTS (default /data/venvs/edge-tts/bin/edge-tts)
"""

from __future__ import annotations

import html
import json
import os
import pathlib
import re
import subprocess
import time

from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
TRACK = HERE.parents[1]
OUT = HERE / "out"
TRANSCRIPT = (TRACK / "docs/results/make_run_transcript.txt").read_text(encoding="utf-8")
EVAL = json.loads((TRACK / "docs/results/eval_report.json").read_text(encoding="utf-8"))
SCENES = json.loads((HERE / "scenes.json").read_text(encoding="utf-8"))
EDGE_TTS = os.environ.get("EDGE_TTS", "/data/venvs/edge-tts/bin/edge-tts")
GTTS = os.environ.get("GTTS", "gtts-cli")
VOICE, RATE = "en-US-AndrewNeural", "+7%"
PAD = 0.7  # seconds of silence after each scene's voice

CSS = """
html,body{margin:0;width:1920px;height:1080px;background:#0d1117;color:#c9d1d9;
  font-family:'JetBrains Mono','DejaVu Sans Mono',monospace;overflow:hidden}
.top{height:64px;display:flex;align-items:center;gap:18px;padding:0 48px;background:#161b22;
  border-bottom:1px solid #30363d;font:600 24px 'DejaVu Sans',sans-serif;color:#e6edf3}
.top .tag{font:500 18px 'DejaVu Sans',sans-serif;color:#8b949e}
.top .dot{width:12px;height:12px;border-radius:50%;background:#3fb950}
pre{margin:0;padding:26px 48px;font-size:23px;line-height:1.42;white-space:pre-wrap}
.h{color:#58a6ff;font-weight:700}.ok{color:#3fb950;font-weight:700}.ph{color:#d2a8ff}
.cap{position:absolute;left:0;right:0;bottom:0;padding:22px 64px 28px;background:rgba(1,4,9,.92);
  border-top:1px solid #30363d;font:500 30px/1.35 'DejaVu Sans',sans-serif;color:#f0f6fc}
table{margin:150px auto 0;border-collapse:collapse;font:36px 'DejaVu Sans',sans-serif}
td,th{border:1px solid #30363d;padding:16px 26px;text-align:left}
th{background:#161b22;color:#8b949e;font-weight:600}
td.n{color:#3fb950;font-weight:700;text-align:right}td.r{color:#f85149;font-weight:700;text-align:right}td.m{color:#8b949e;text-align:right}
.big{position:absolute;top:300px;left:0;right:0;text-align:center;font-family:'DejaVu Sans',sans-serif}
.big h1{font-size:84px;margin:0;color:#f0f6fc}.big p{font-size:34px;color:#8b949e;margin:22px 0}
.big code{font:40px 'DejaVu Sans Mono',monospace;color:#3fb950;background:#161b22;padding:10px 22px;border-radius:8px}
"""


def section(start: str, end: str | None = None) -> str:
    lines = TRANSCRIPT.splitlines()
    s = next(i for i, line in enumerate(lines) if line.startswith(start))
    e = next((i for i, line in enumerate(lines) if end and line.startswith(end)), len(lines))
    return "\n".join(lines[s:e]).rstrip()


def colorize(text: str) -> str:
    t = html.escape(text)
    t = re.sub(r"^(== .*)$", r'<span class="h">\1</span>', t, flags=re.M)
    t = t.replace("[ok]", '<span class="ok">[ok]</span>')
    return re.sub(r"(\[\[[A-Z_]+_[0-9a-f]{4,12}\]\])", r'<span class="ph">\1</span>', t)


def body_for(kind: str) -> tuple[str, str]:
    if kind == "dossier":
        return "Client dossier (synthetic, de / fr / it)", f"<pre>{colorize(section('== 2.', '== 3.'))}</pre>"
    if kind == "runtime":
        return "make run · air-gapped Docker network", f"<pre>{colorize(section('== 1.', '== 2.'))}</pre>"
    if kind == "wire":
        return "What Apertus received, captured on the wire", f"<pre>{colorize(section('== 3.', '== 4.'))}</pre>"
    if kind == "answer":
        return "Apertus answer → values restored by the proxy", f"<pre>{colorize(section('== 4.', '== 6.'))}</pre>"
    if kind == "checks":
        return "Leak checks · hash-chained audit · tamper test", f"<pre>{colorize(section('== 6.', '== Done'))}</pre>"
    if kind == "eval":
        lr, fid = EVAL["leak_rate"], EVAL["task_fidelity_mean"]
        rows = [
            ("Raw note straight to Apertus (no proxy)", "100 %", f"{fid['baseline_raw']*100:.1f} %"),
            ("Swiss identifier patterns only", f"{lr['patterns']*100:.1f} %", "n/a"),
            ("Patterns + local Apertus NER", f"{lr['patterns+apertus']*100:.1f} %", f"{fid['shield']*100:.1f} %"),
        ]
        def cls(v: str, leak: bool) -> str:
            if v == "n/a":
                return "m"
            return "r" if leak and float(v.split()[0]) > 10 else "n"
        trs = "".join(f"<tr><td>{a}</td><td class='{cls(b, True)}'>{b}</td><td class='{cls(c, False)}'>{c}</td></tr>"
                      for a, b, c in rows)
        return (f"Evaluation · {EVAL['cases']} generated client notes · {EVAL['values_total']} sensitive values",
                f"<table><tr><th>Setup</th><th>Values reaching the model</th><th>Task fidelity</th></tr>{trs}</table>")
    return "", ("<div class='big'><h1>Treuhand Shield</h1>"
                "<p>Swiss open model · local PII detection · reversible tokens · hash-chained audit</p>"
                "<p><code>cd track_2b &amp;&amp; make run</code></p>"
                "<p>github.com/ctmakc/treuhand-shield · Apache-2.0</p></div>")


def tts(text: str, mp3: pathlib.Path) -> str:
    """edge-tts first (3 tries); if the service refuses, gTTS sped up 1.15x with ffmpeg."""
    if mp3.exists() and mp3.stat().st_size > 0:
        return "cached"
    for attempt in range(3):
        r = subprocess.run([EDGE_TTS, "--voice", VOICE, f"--rate={RATE}", "--text", text,
                            "--write-media", str(mp3)], capture_output=True, text=True)
        if r.returncode == 0 and mp3.exists() and mp3.stat().st_size > 0:
            return "edge-tts"
        time.sleep(2 + attempt)
    raw = mp3.with_suffix(".gtts.mp3")
    subprocess.run([GTTS, text, "-l", "en", "--tld", "com", "-o", str(raw)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-af", "atempo=1.15",
                    str(mp3)], check=True)
    return "gtts"


def duration(path: pathlib.Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def main() -> int:
    OUT.mkdir(exist_ok=True)
    segs, srt, t = [], [], 0.0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        for i, sc in enumerate(SCENES, 1):
            mp3 = OUT / f"{sc['id']}.mp3"
            print(sc["id"], tts(sc["say"], mp3), flush=True)
            dur = duration(mp3) + PAD
            title, body = body_for(sc["kind"])
            top = f"<div class='top'><span class='dot'></span>{html.escape(title)}<span class='tag'>Treuhand Shield · Apertus v1.5 8B</span></div>" if title else ""
            page.set_content(f"<html><head><style>{CSS}</style></head><body>{top}{body}"
                             f"<div class='cap'>{html.escape(sc['say'])}</div></body></html>")
            png = OUT / f"{sc['id']}.png"
            page.screenshot(path=str(png))
            seg = OUT / f"{sc['id']}.mp4"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30",
                            "-i", str(png), "-i", str(mp3), "-af", f"apad=pad_dur={PAD}",
                            "-t", f"{dur:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-tune",
                            "stillimage", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", str(seg)],
                           check=True)
            segs.append(seg)
            srt.append(f"{i}\n{srt_time(t)} --> {srt_time(t + dur - PAD)}\n{sc['say']}\n")
            t += dur
        browser.close()
    lst = OUT / "segments.txt"
    lst.write_text("".join(f"file '{s.name}'\n" for s in segs))
    final = OUT / "treuhand_shield_demo.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i",
                    str(lst), "-c", "copy", "-movflags", "+faststart", str(final)], check=True)
    (OUT / "treuhand_shield_demo.srt").write_text("\n".join(srt), encoding="utf-8")
    print(f"{final}  {duration(final):.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
