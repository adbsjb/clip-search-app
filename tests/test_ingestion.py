import tempfile
from pathlib import Path

import app as bit_search_app
from app import delete_media_file, ingest_media_directory


def test_ingest_and_delete_media_files(tmp_path: Path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "episode-1.mp4").write_bytes(b"fake video")
    (media_dir / "episode-2.mkv").write_bytes(b"fake video")

    clips = ingest_media_directory(media_dir, existing_clips=[])

    assert len(clips) == 2
    assert {clip["source"] for clip in clips} == {"episode-1.mp4", "episode-2.mkv"}
    assert clips[0]["title"].startswith("Episode")

    (media_dir / "readme.txt").write_text("should be ignored")
    clips = ingest_media_directory(media_dir, clips)

    assert (media_dir / "readme.txt").exists()
    assert len(clips) == 2
    assert {clip["source"] for clip in clips} == {"episode-1.mp4", "episode-2.mkv"}

    trickplay_dir = media_dir / "episode-1.mp4.trickplay"
    trickplay_dir.mkdir()
    (trickplay_dir / "index.html").write_text("trickplay content")
    clips = ingest_media_directory(media_dir, clips)

    assert trickplay_dir.exists()
    assert len(clips) == 2
    assert {clip["source"] for clip in clips} == {"episode-1.mp4", "episode-2.mkv"}

    clips = delete_media_file(media_dir, "episode-2.mkv", clips)

    assert not (media_dir / "episode-2.mkv").exists()
    assert all(clip["source"] != "episode-2.mkv" for clip in clips)


def test_ingest_uses_sidecar_subtitles(tmp_path: Path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    video_path = media_dir / "episode-1.mp4"
    video_path.write_bytes(b"fake video")

    subtitle_path = media_dir / "episode-1.en.srt"
    subtitle_path.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nThis is the first line.\n\n2\n00:00:03,500 --> 00:00:05,500\nThis is the second line.\n",
        encoding="utf-8",
    )

    clips = ingest_media_directory(media_dir, existing_clips=[])

    assert len(clips) == 2
    assert all(clip["source"] == "episode-1.mp4" for clip in clips)
    assert clips[0]["quote"] == "This is the first line."
    assert clips[1]["quote"] == "This is the second line."
    assert clips[0]["generated"] is True
    assert clips[1]["generated"] is True
    assert clips[0]["subtitle_source"] == "episode-1.en.srt"
    assert clips[1]["subtitle_source"] == "episode-1.en.srt"


def test_ingest_prefers_english_sidecar_over_foreign_languages(tmp_path: Path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    video_path = media_dir / "episode-1.mp4"
    video_path.write_bytes(b"fake video")

    (media_dir / "episode-1.fr.srt").write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nCeci est une ligne.\n",
        encoding="utf-8",
    )
    (media_dir / "episode-1.en.srt").write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nThis is the English line.\n",
        encoding="utf-8",
    )

    clips = ingest_media_directory(media_dir, existing_clips=[])

    assert len(clips) == 1
    assert clips[0]["source"] == "episode-1.mp4"
    assert clips[0]["quote"] == "This is the English line."
    assert clips[0]["generated"] is True


def test_extract_show_name_from_filename():
    assert bit_search_app.extract_show_name(Path("Aunty Donna - S01E01 - Housemates.mp4")) == "Aunty Donna"
    assert bit_search_app.extract_show_name(Path("Some Show - S02E10 - Finale.mkv")) == "Some Show"
    assert bit_search_app.extract_show_name(Path("Mystery.mp4")) == "Mystery"
    assert bit_search_app.extract_show_name(Path("A Show - Extra.mp4")) == "A Show"


def test_ingest_ignores_non_english_only_sidecars(tmp_path: Path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    video_path = media_dir / "episode-1.mp4"
    video_path.write_bytes(b"fake video")

    (media_dir / "episode-1.fr.srt").write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nCeci est une ligne.\n",
        encoding="utf-8",
    )

    clips = ingest_media_directory(media_dir, existing_clips=[])

    assert len(clips) == 1
    assert clips[0]["source"] == "episode-1.mp4"
    assert clips[0].get("generated") is not True
    assert clips[0]["quote"] == "episode 1"


def test_ingest_ignores_untagged_sidecars_when_only_foreign_language_is_available(tmp_path: Path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    video_path = media_dir / "episode-1.mp4"
    video_path.write_bytes(b"fake video")

    (media_dir / "episode-1.srt").write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nCeci est une ligne.\n",
        encoding="utf-8",
    )

    clips = ingest_media_directory(media_dir, existing_clips=[])

    assert len(clips) == 1
    assert clips[0]["source"] == "episode-1.mp4"
    assert clips[0].get("generated") is not True
    assert clips[0]["quote"] == "episode 1"


def test_ingest_follows_symlinked_media_files(tmp_path: Path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    real_dir = tmp_path / "real_videos"
    real_dir.mkdir()
    real_video = real_dir / "episode-1.mp4"
    real_video.write_bytes(b"fake video")
    symlink_video = media_dir / "episode-1.mp4"
    symlink_video.symlink_to(real_video)

    clips = ingest_media_directory(media_dir, existing_clips=[])

    assert len(clips) == 1
    assert clips[0]["source"] == "episode-1.mp4"
    assert clips[0].get("generated") is not True


def test_ingest_creates_metadata_file_when_missing(tmp_path: Path, monkeypatch):
    import json

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    video_path = media_dir / "episode-1.mp4"
    video_path.write_bytes(b"fake video")

    metadata_path = tmp_path / "clips.json"
    monkeypatch.setattr(bit_search_app, "MEDIA_DIR", media_dir)
    monkeypatch.setattr(bit_search_app, "METADATA_PATH", metadata_path)

    clips = bit_search_app.ingest_media_directory(media_dir, existing_clips=[])
    assert not metadata_path.exists()

    # Persist after ingest should create clips.json in the correct format.
    bit_search_app.BitSearchHandler.persist_clips(object(), clips)

    assert metadata_path.exists()
    assert json.loads(metadata_path.read_text(encoding="utf-8")) == clips


def test_ingest_skips_reprocessing_when_source_unchanged(tmp_path: Path, monkeypatch):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    video_path = media_dir / "episode-1.mp4"
    video_path.write_bytes(b"fake video")

    calls = []

    def fake_sidecar(media_path, subtitle_paths):
        calls.append(media_path)
        return ([{"start": 1.0, "end": 2.0, "text": "Hello world."}], "episode-1.en.srt")

    monkeypatch.setattr(bit_search_app, "get_sidecar_subtitles", fake_sidecar)

    clips = bit_search_app.ingest_media_directory(media_dir, existing_clips=[])
    assert len(clips) == 1
    assert clips[0]["generated"] is True
    assert "source_mtime" in clips[0]
    assert "source_size" in clips[0]

    calls.clear()
    clips2 = bit_search_app.ingest_media_directory(media_dir, existing_clips=clips)
    assert len(clips2) == 1
    assert clips2[0]["generated"] is True
    assert calls == []
    assert clips2[0]["source_mtime"] == clips[0]["source_mtime"]
    assert clips2[0]["source_size"] == clips[0]["source_size"]
