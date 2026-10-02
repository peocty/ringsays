"""Compose the two demo recordings into one 1920x1080 walkthrough with captions (ffmpeg).

    python3 compose.py <folder>    # folder written by the demo test: raw/agent.webm, raw/phone.webm, timeline.json

Writes <folder>/ringsays-demo.mp4 (H.264, plays everywhere) and a poster frame.
"""

from __future__ import annotations

import json
import subprocess
import textwrap
import sys
from pathlib import Path

W, H = 1920, 1080
BG = "0x0E2A47"
ACCENT = "0xF5A623"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
AGENT = (40, 120, 1400, 875)  # x, y, width, height
PHONE_H = 875
PHONE = (1478, 120, round(PHONE_H * 360 / 780), PHONE_H)


def main(folder: Path) -> None:
    t = json.loads((folder / "timeline.json").read_text())
    caps = folder / "captions"
    caps.mkdir(exist_ok=True)
    duration = t["duration"] + 1.5
    draws = []
    for i, (c, nxt) in enumerate(zip(t["captions"], t["captions"][1:])):
        if not c["text"]:
            continue
        f = caps / f"{i:02d}.txt"
        f.write_text(textwrap.fill(c["text"], 78))
        draws.append(
            f"drawtext=fontfile={FONT}:textfile={f}:fontsize=31:line_spacing=10:fontcolor=white:"
            f"x=(w-text_w)/2:y=1040-text_h/2:enable='between(t,{c['at']:.2f},{nxt['at']:.2f})'"
        )
    title = caps / "title.txt"
    title.write_text("RingSays  ·  verified call requests  ·  Mock Bank walkthrough (demo data)")
    left = caps / "left.txt"
    left.write_text("Bank agent console")
    right = caps / "right.txt"
    right.write_text("Customer's bank app")
    ax, ay, aw, ah = AGENT
    px, py, pw, ph = PHONE
    graph = ";".join(
        [
            f"color=c={BG}:s={W}x{H}:d={duration:.2f}:r=25[bg]",
            f"[0:v]trim=start={t['agentOffset']:.3f},setpts=PTS-STARTPTS,scale={aw}:{ah},fps=25[a]",
            f"[1:v]trim=start={t['phoneOffset']:.3f},setpts=PTS-STARTPTS,scale={pw}:{ph},fps=25[p]",
            f"[bg][a]overlay={ax}:{ay}:eof_action=repeat[s1]",
            f"[s1][p]overlay={px}:{py}:eof_action=repeat[s2]",
            "[s2]"
            + ",".join(
                [
                    f"drawbox=x={ax - 3}:y={ay - 3}:w={aw + 6}:h={ah + 6}:color=white@0.25:t=3",
                    f"drawbox=x={px - 3}:y={py - 3}:w={pw + 6}:h={ph + 6}:color=white@0.25:t=3",
                    f"drawbox=x=0:y=1002:w={W}:h=78:color=black@0.35:t=fill",
                    f"drawbox=x=0:y=0:w={W}:h=6:color={ACCENT}:t=fill",
                    f"drawtext=fontfile={BOLD}:textfile={title}:fontsize=30:fontcolor=white:x=40:y=34",
                    f"drawtext=fontfile={FONT}:textfile={left}:fontsize=22:fontcolor=white@0.8:x={ax}:y=86",
                    f"drawtext=fontfile={FONT}:textfile={right}:fontsize=22:fontcolor=white@0.8:x={px}:y=86",
                    *draws,
                ]
            )
            + "[v]",
        ]
    )
    mp4 = folder / "ringsays-demo.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(folder / "raw" / "agent.webm"),
            "-i", str(folder / "raw" / "phone.webm"),
            "-filter_complex", graph, "-map", "[v]",
            "-t", f"{duration:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "22",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4),
        ],
        check=True,
    )  # fmt: skip
    poster = folder / "ringsays-demo-poster.png"
    at = next((c["at"] for c in t["captions"] if "verified by RingSays" in c["text"]), 10) + 2
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", str(mp4), "-frames:v", "1", str(poster)],
        check=True,
    )
    print(f"wrote {mp4} ({duration:.0f} s) and {poster}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
