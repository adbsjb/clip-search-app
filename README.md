# Clip Search

A small self-hosted web app for searching clip metadata and embedded subtitles, playing a specific clip in the browser, and exporting a cached GIF.

## Run locally

```bash
cd /home/alex/clip-search-app
python3 -m pip install -r requirements.txt
python3 app.py
```

This app uses `ffmpeg` and `ffprobe` to extract embedded subtitles and generate clip exports. It currently exports only GIFs for animated clips, which is the most compatible format for inline image sharing in many chat apps.

Open http://127.0.0.1:8001/.

## Run with Docker Compose

On your server, from the project directory:

```bash
docker compose up -d --build
```

Then browse to http://\<your-server\>:8001/.

## Data layout

- Put your source videos in `data/media/`.
- If your videos include embedded subtitles, or you have matching sidecar subtitle files (`.srt`, `.vtt`, `.ass`, `.sub`), the app can automatically generate searchable clips from English subtitle cues.
- Put clip metadata in `data/clips.json` if you want manual clips as well.
- Each clip entry should look like:

```json
{
  "id": "my-bit",
  "title": "My Bit",
  "quote": "phrase from the bit",
  "description": "Short description",
  "subtitle": "Text to display in exported GIF",
  "source": "video.mp4",
  "start": 12.5,
  "duration": 4.0
}
```

The app will generate exports on first request and cache them under `data/cache/` for later reuse.
