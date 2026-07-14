#!/usr/bin/env python3
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
MEDIA_DIR = DATA_DIR / "media"
CACHE_DIR = DATA_DIR / "cache"
METADATA_PATH = DATA_DIR / "clips.json"
STATIC_DIR = ROOT / "static"


def search_clips(clips: list[dict], query: str) -> list[dict]:
    if not query:
        return clips[:5]

    searchable = []
    normalized_query = re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()
    if not normalized_query:
        return clips[:5]

    for clip in clips:
        haystack = " ".join([
            clip.get("title", ""),
            clip.get("quote", ""),
            clip.get("subtitle", ""),
            clip.get("description", ""),
        ]).lower()
        haystack = re.sub(r"[^a-z0-9]+", " ", haystack)
        if normalized_query in haystack:
            searchable.append(clip)
    return searchable[:10]


VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".avi", ".m4v", ".webm"}
SUBTITLE_SUFFIXES = {".srt", ".vtt", ".ass", ".sub"}


def parse_timestamp(timestamp: str) -> float:
    timestamp = timestamp.strip().replace(",", ".")
    parts = timestamp.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, seconds = parts
    else:
        raise ValueError(f"invalid timestamp: {timestamp}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def load_clips_file() -> list[dict]:
    if not METADATA_PATH.exists():
        return []
    with METADATA_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def persist_clips_file(clips: list[dict]) -> None:
    METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with METADATA_PATH.open("w", encoding="utf-8") as fh:
        json.dump(clips, fh, indent=2)


def parse_subtitles(raw_srt: str) -> list[dict]:
    cues = []
    lines = raw_srt.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0

    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue

        if lines[index].strip().isdigit():
            index += 1
            if index >= len(lines):
                break

        time_line = lines[index].strip()
        index += 1

        if "-->" not in time_line:
            continue

        try:
            start_text, end_text = [part.strip() for part in time_line.split("-->", 1)]
            start = parse_timestamp(start_text)
            end = parse_timestamp(end_text)
        except ValueError:
            continue

        text_lines = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index].strip())
            index += 1

        text = " ".join(text_lines).strip()
        if text:
            cues.append({"start": start, "end": end, "text": text})

    return cues


def is_english_language_tag(tag: str | None) -> bool:
    if not tag:
        return False
    tag = tag.strip().lower()
    return tag.startswith("en") or tag == "english"


def is_english_subtitle_filename(path: Path) -> bool:
    stem = path.stem.lower()
    if re.search(r"[._-](en|eng|english)$", stem):
        return True
    parts = path.name.lower().split(".")
    if len(parts) >= 3 and parts[-2] in {"en", "eng", "english"}:
        return True
    return False


def get_embedded_subtitles(media_path: Path) -> tuple[list[dict], str] | tuple[[], None]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-loglevel",
                "error",
                "-select_streams",
                "s",
                "-show_entries",
                "stream=index:stream_tags=language,title",
                "-of",
                "json",
                str(media_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [], None

    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError:
        return [], None

    if not streams:
        return [], None

    english_stream = next(
        (s for s in streams if is_english_language_tag(s.get("tags", {}).get("language", ""))),
        None,
    )
    if not english_stream:
        return [], None

    stream_index = english_stream.get("index")
    source_name = english_stream.get("tags", {}).get("title") or english_stream.get("tags", {}).get("language") or f"embedded stream {stream_index}"
    try:
        subtitle_result = subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-i",
                str(media_path),
                "-map",
                f"0:{stream_index}",
                "-f",
                "srt",
                "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return [], None

    cues = parse_subtitles(subtitle_result.stdout)
    return cues, source_name


def get_sidecar_subtitles(media_path: Path, subtitle_paths: list[Path]) -> tuple[list[dict], str] | tuple[[], None]:
    candidates = [
        path
        for path in subtitle_paths
        if path.parent == media_path.parent
        and any(
            path.name == f"{media_path.stem}{ext}"
            or path.name.startswith(f"{media_path.stem}.")
            for ext in SUBTITLE_SUFFIXES
        )
    ]
    english_candidates = [path for path in candidates if is_english_subtitle_filename(path)]
    if not english_candidates:
        return [], None

    for path in sorted(english_candidates, key=lambda path: path.name):
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        cues = parse_subtitles(raw)
        if cues:
            return cues, path.name
    return [], None


def parse_clip_description(media_path: Path) -> str:
    stem = media_path.stem
    match = re.match(r"^(?P<show>.+?)\s*-\s*S(?P<season>\d+)E(?P<episode>\d+)\s*-\s*(?P<episode_name>.+)$", stem, re.IGNORECASE)
    if match:
        show = match.group("show").strip()
        season = int(match.group("season"))
        episode = int(match.group("episode"))
        episode_name = match.group("episode_name").strip()
        return f"From {show} Season {season} Episode {episode} — {episode_name}"
    return f"From {stem}" if stem else "From unknown source"


def extract_show_name(media_path: Path) -> str:
    stem = media_path.stem
    match = re.match(r"^(?P<show>.+?)\s*-\s*S\d+E\d+\s*-", stem, re.IGNORECASE)
    if match:
        return match.group("show").strip()
    if " - " in stem:
        return stem.split(" - ", 1)[0].strip()
    return stem.strip() or "Unknown"


def get_indexed_shows(clips: list[dict]) -> list[str]:
    shows = set()
    for clip in clips:
        source = clip.get("source")
        if not source:
            continue
        show = extract_show_name(Path(source))
        if show:
            shows.add(show)
    return sorted(shows)


def format_clip_title(media_path: Path, cue: dict, index: int) -> str:
    return cue["text"].strip()


def generate_clip_for_subtitle(media_path: Path, relative_path: str, cue: dict, index: int, subtitle_source: str, source_size: int, source_mtime: int) -> dict:
    duration = max(2.0, cue["end"] - cue["start"])
    return {
        "id": slugify(f"{relative_path}-{int(cue['start']*1000)}-{index}"),
        "title": format_clip_title(media_path, cue, index),
        "quote": cue["text"],
        "description": parse_clip_description(media_path),
        "subtitle": cue["text"],
        "subtitle_source": subtitle_source,
        "source_size": source_size,
        "source_mtime": source_mtime,
        "source": relative_path,
        "start": cue["start"],
        "duration": duration,
        "generated": True,
    }


def get_media_file_state(media_path: Path) -> tuple[int, int]:
    stat = media_path.stat()
    return stat.st_size, stat.st_mtime_ns


def source_has_fresh_generated_clips(media_path: Path, clips: list[dict]) -> bool:
    if not clips:
        return False
    size, mtime = get_media_file_state(media_path)
    for clip in clips:
        if clip.get("generated") is not True:
            continue
        if clip.get("source_size") != size or clip.get("source_mtime") != mtime:
            return False
    return True


def iter_media_paths(media_dir: Path):
    seen_dirs = set()
    for dirpath, dirnames, filenames in os.walk(media_dir, followlinks=True):
        try:
            stat = os.stat(dirpath)
        except OSError:
            continue
        dir_id = (stat.st_dev, stat.st_ino)
        if dir_id in seen_dirs:
            continue
        seen_dirs.add(dir_id)

        for filename in filenames:
            yield Path(dirpath) / filename


def ingest_media_directory(media_dir: Path, existing_clips: list[dict]) -> list[dict]:
    media_dir.mkdir(parents=True, exist_ok=True)

    video_files = []
    subtitle_files = []
    for path in sorted(iter_media_paths(media_dir), key=lambda p: str(p)):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in VIDEO_SUFFIXES:
            video_files.append(path)
        elif suffix in SUBTITLE_SUFFIXES:
            subtitle_files.append(path)
        else:
            continue

    valid_sources = {path.relative_to(media_dir).as_posix() for path in video_files}
    existing_by_source = {}
    for clip in existing_clips:
        source = clip.get("source")
        if source and source in valid_sources:
            existing_by_source.setdefault(source, []).append(clip)

    clips = []
    for media_path in video_files:
        relative_path = media_path.relative_to(media_dir).as_posix()
        existing_source_clips = existing_by_source.get(relative_path, [])
        manual_source_clips = [clip for clip in existing_source_clips if clip.get("generated") is not True]
        existing_generated_clips = [clip for clip in existing_source_clips if clip.get("generated") is True]

        if source_has_fresh_generated_clips(media_path, existing_generated_clips):
            clips.extend(manual_source_clips + existing_generated_clips)
            continue

        cues, source = get_sidecar_subtitles(media_path, subtitle_files)
        if not cues:
            cues, source = get_embedded_subtitles(media_path)

        if cues:
            size, mtime = get_media_file_state(media_path)
            for index, cue in enumerate(cues):
                clips.append(
                    generate_clip_for_subtitle(media_path, relative_path, cue, index, source or "unknown", size, mtime)
                )
        else:
            existing = next(
                (clip for clip in existing_source_clips if clip.get("generated") is not True),
                None,
            )
            if existing:
                clip = dict(existing)
                clip["id"] = clip.get("id") or slugify(relative_path)
                clip["source"] = relative_path
                clip["title"] = clip.get("title") or clip.get("subtitle", "")
                clip["quote"] = clip.get("quote") or clip.get("subtitle", "")
                clip["description"] = clip.get("description") or parse_clip_description(media_path)
                clip["subtitle"] = clip.get("subtitle") or ""
                clip["start"] = clip.get("start", 0.0)
                clip["duration"] = clip.get("duration", 3.0)
                clips.append(clip)
            else:
                stem = media_path.stem.replace("_", " ").replace("-", " ")
                title = " ".join(part.capitalize() for part in re.split(r"\s+", stem) if part)
                clips.append(
                    {
                        "id": slugify(relative_path),
                        "title": title or media_path.stem,
                        "quote": title.lower(),
                        "description": parse_clip_description(media_path),
                        "subtitle": "",
                        "source": relative_path,
                        "start": 0.0,
                        "duration": 3.0,
                    }
                )

    return clips


def delete_media_file(media_dir: Path, filename: str, existing_clips: list[dict]) -> list[dict]:
    media_path = media_dir / filename
    if media_path.exists():
        media_path.unlink()

    return ingest_media_directory(media_dir, [clip for clip in existing_clips if clip.get("source") != filename])


def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "clip"


class BitSearchHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self.send_static_file("static/index.html")
            return

        if path.startswith("/api/search"):
            self.handle_search(parsed)
            return

        if path.startswith("/api/clip"):
            self.handle_clip(parsed)
            return

        if path.startswith("/api/export"):
            self.handle_export(parsed)
            return

        if path.startswith("/api/ingest"):
            self.handle_ingest(parsed)
            return

        if path.startswith("/api/delete"):
            self.handle_delete(parsed)
            return

        if path.startswith("/api/shows"):
            self.handle_shows(parsed)
            return

        if path.startswith("/media/"):
            relative_path = unquote(path[len("/media/"):])
            self.serve_path(MEDIA_DIR / relative_path, default_type="video/mp4")
            return

        if path.startswith("/cache/"):
            self.serve_path(CACHE_DIR / path[len("/cache/"):])
            return

        if path.startswith("/static/"):
            self.send_static_file(path[1:])
            return

        super().do_GET()

    def do_HEAD(self):
        self.do_GET()

    def log_message(self, format, *args):
        sys.stdout.write(f"{self.address_string()} - - [{self.log_date_time_string()}] {format % args}\n")

    def send_static_file(self, relative_path: str):
        file_path = (ROOT / relative_path).resolve()
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404)
            return
        self.serve_path(file_path)

    def serve_path(self, file_path: Path, default_type: str | None = None):
        try:
            resolved = file_path.resolve()
        except FileNotFoundError:
            self.send_error(404)
            return

        if not resolved.exists() or not resolved.is_file():
            self.send_error(404)
            return

        if ROOT not in resolved.parents and str(resolved) != str(ROOT):
            self.send_error(403)
            return

        content_type, _ = mimetypes.guess_type(str(resolved))
        if not content_type and default_type:
            content_type = default_type
        if not content_type:
            content_type = "application/octet-stream"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        with resolved.open("rb") as fh:
            shutil.copyfileobj(fh, self.wfile)

    def handle_search(self, parsed):
        params = parse_qs(parsed.query)
        query = (params.get("q", [""])[0] or "").strip()
        clips = self.load_clips()
        self.send_json({"results": search_clips(clips, query)})

    def handle_clip(self, parsed):
        params = parse_qs(parsed.query)
        clip_id = (params.get("id", [""])[0] or "").strip()
        clips = self.load_clips()
        clip = next((c for c in clips if c.get("id") == clip_id), None)
        if not clip:
            self.send_json({"error": "clip not found"}, status=404)
            return

        clip_payload = dict(clip)
        clip_payload["video_url"] = f"/media/{quote(clip['source'])}#t={clip['start']},{clip['start'] + clip['duration']}"
        self.send_json(clip_payload)

    def handle_export(self, parsed):
        params = parse_qs(parsed.query)
        clip_id = (params.get("id", [""])[0] or "").strip()
        export_format = (params.get("format", ["gif"])[0] or "gif").strip().lower()
        if export_format not in {"gif", "mp4"}:
            self.send_json({"error": "unsupported format"}, status=400)
            return

        clips = self.load_clips()
        clip = next((c for c in clips if c.get("id") == clip_id), None)
        if not clip:
            self.send_json({"error": "clip not found"}, status=404)
            return

        output_path = self.ensure_export(clip, export_format)
        public_url = f"/cache/{output_path.name}"
        self.send_json({"url": public_url, "format": export_format})

    def handle_ingest(self, parsed):
        clips = self.load_clips()
        updated_clips = ingest_media_directory(MEDIA_DIR, clips)
        self.persist_clips(updated_clips)
        self.send_json({"clips": updated_clips})

    def handle_delete(self, parsed):
        params = parse_qs(parsed.query)
        filename = (params.get("file", [""])[0] or "").strip()
        if not filename:
            self.send_json({"error": "missing file"}, status=400)
            return

        clips = self.load_clips()
        updated_clips = delete_media_file(MEDIA_DIR, filename, clips)
        self.persist_clips(updated_clips)
        self.send_json({"clips": updated_clips})

    def handle_shows(self, parsed):
        clips = self.load_clips()
        shows = get_indexed_shows(clips)
        self.send_json({"shows": shows})

    def ensure_export(self, clip: dict, export_format: str = "gif") -> Path:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        extension = "gif" if export_format == "gif" else "mp4"
        output_path = CACHE_DIR / f"{clip['id']}.{extension}"
        if output_path.exists():
            return output_path

        source_path = MEDIA_DIR / clip["source"]
        if not source_path.exists():
            raise FileNotFoundError(f"missing source video: {source_path}")

        subtitle = (clip.get("subtitle") or "").strip()
        with tempfile.TemporaryDirectory(prefix=f"{clip['id']}_", dir=str(CACHE_DIR)) as temp_dir_name:
            temp_dir = Path(temp_dir_name)

            if export_format == "mp4":
                srt_path = temp_dir / "cue.srt"
                duration = max(2.0, clip.get("duration", 3.0))

                def fmt_ts(t: float) -> str:
                    hours = int(t // 3600)
                    minutes = int((t % 3600) // 60)
                    seconds = int(t % 60)
                    millis = int((t - int(t)) * 1000)
                    return f"{hours:01d}:{minutes:02d}:{seconds:02d},{millis:03d}"

                start_ts = fmt_ts(0.0)
                end_ts = fmt_ts(duration)
                srt_content = f"1\n{start_ts} --> {end_ts}\n{subtitle}\n\n"
                srt_path.write_text(srt_content, encoding="utf-8")

                vf_filter = f"subtitles={srt_path.as_posix()}:force_style='Fontsize=30,Outline=1'"
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(clip["start"]),
                    "-t", str(duration),
                    "-i", str(source_path),
                    "-vf", vf_filter,
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-movflags", "+faststart",
                    str(output_path),
                ]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return output_path

            if export_format == "gif":
                mp4_export = self.ensure_export(clip, "mp4")
                palette_path = temp_dir / "palette.png"
                scale_filter = "fps=12,scale=min(640,iw):-2:flags=lanczos"
                try:
                    subprocess.run([
                        "ffmpeg", "-y",
                        "-i", str(mp4_export),
                        "-vf", f"{scale_filter},palettegen",
                        str(palette_path),
                    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                    subprocess.run([
                        "ffmpeg", "-y",
                        "-i", str(mp4_export),
                        "-i", str(palette_path),
                        "-filter_complex", f"[0:v]{scale_filter}[x];[x][1:v]paletteuse",
                        "-loop", "0",
                        str(output_path),
                    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except subprocess.CalledProcessError:
                    # Fallback to a simpler GIF generation pipeline if scaling/palette generation fails.
                    subprocess.run([
                        "ffmpeg", "-y",
                        "-i", str(mp4_export),
                        "-vf", "fps=12,palettegen",
                        str(palette_path),
                    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                    subprocess.run([
                        "ffmpeg", "-y",
                        "-i", str(mp4_export),
                        "-i", str(palette_path),
                        "-filter_complex", "[0:v]fps=12[x];[x][1:v]paletteuse",
                        "-loop", "0",
                        str(output_path),
                    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return output_path

            raise RuntimeError(f"unsupported export format: {export_format}")

        return output_path

    def add_subtitle_overlay(self, image: Image.Image, subtitle: str) -> Image.Image:
        if not subtitle:
            return image
        draw = ImageDraw.Draw(image, "RGBA")
        width, height = image.size
        font_size = max(24, min(56, height // 14))
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
        except OSError:
            try:
                font = ImageFont.truetype("arial.ttf", font_size)
            except OSError:
                font = ImageFont.load_default()

        # Wrap text to fit within 80% of the image width.
        max_text_width = int(width * 0.8)
        subtitle_lines = []
        for paragraph in subtitle.replace('\r', '').split('\n'):
            if not paragraph.strip():
                subtitle_lines.append("")
                continue
            words = paragraph.split()
            current_line = []
            for word in words:
                test_line = " ".join(current_line + [word]) if current_line else word
                bbox = draw.textbbox((0, 0), test_line, font=font)
                if bbox[2] - bbox[0] <= max_text_width or not current_line:
                    current_line.append(word)
                else:
                    subtitle_lines.append(" ".join(current_line))
                    current_line = [word]
            if current_line:
                subtitle_lines.append(" ".join(current_line))

        line_sizes = [draw.textbbox((0, 0), line, font=font) for line in subtitle_lines]
        text_width = max((bbox[2] - bbox[0]) for bbox in line_sizes)
        line_height = max((bbox[3] - bbox[1]) for bbox in line_sizes)
        total_height = line_height * len(subtitle_lines) + (len(subtitle_lines) - 1) * 6

        x = (width - text_width) // 2
        y = height - total_height - 30
        rect_x0 = x - 16
        rect_y0 = y - 10
        rect_x1 = x + text_width + 16
        rect_y1 = y + total_height + 10
        draw.rounded_rectangle((rect_x0, rect_y0, rect_x1, rect_y1), radius=10, fill=(0, 0, 0, 180))

        for index, line in enumerate(subtitle_lines):
            line_y = y + index * (line_height + 6)
            draw.text((x, line_y), line, font=font, fill=(255, 255, 255, 255))
        return image

    def load_clips(self):
        if not METADATA_PATH.exists():
            return []
        with METADATA_PATH.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def persist_clips(self, clips: list[dict]) -> None:
        METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        with METADATA_PATH.open("w", encoding="utf-8") as fh:
            json.dump(clips, fh, indent=2)

    @staticmethod
    def ensure_data_dirs():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        STATIC_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def ensure_sample_data():
        BitSearchHandler.ensure_data_dirs()
        video_exists = any(
            path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
            for path in MEDIA_DIR.rglob("*")
        )
        if video_exists or METADATA_PATH.exists():
            return

        media_path = MEDIA_DIR / "demo.mp4"
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "testsrc=size=1280x720:rate=24",
            "-f", "lavfi",
            "-i", "sine=frequency=1000:duration=8",
            "-shortest",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(media_path),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        sample_clips = [
            {
                "id": "banana-incident",
                "title": "Banana Incident",
                "quote": "banana",
                "description": "A classic bit about a very serious banana situation.",
                "subtitle": "This is a truly important banana emergency.",
                "source": "demo.mp4",
                "start": 1.2,
                "duration": 3.0,
            },
            {
                "id": "socks-are-serious",
                "title": "Socks Are Serious",
                "quote": "socks",
                "description": "A dramatic bit about socks and how seriously they should be taken.",
                "subtitle": "Socks are a serious matter when you think about it.",
                "source": "demo.mp4",
                "start": 3.8,
                "duration": 3.0,
            },
        ]
        with METADATA_PATH.open("w", encoding="utf-8") as fh:
            json.dump(sample_clips, fh, indent=2)

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    BitSearchHandler.ensure_sample_data()
    clips = load_clips_file()
    updated_clips = ingest_media_directory(MEDIA_DIR, clips)
    if updated_clips != clips:
        persist_clips_file(updated_clips)
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8001"))), BitSearchHandler)
    print(f"Serving bit-search app on http://0.0.0.0:{server.server_address[1]}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
