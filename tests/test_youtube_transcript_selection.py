"""Verify fetch_youtube_transcript's track-selection priority and
language-translation fallback without hitting YouTube's live (IP-block-prone)
endpoint. Mocks YouTubeTranscriptApi.list() to return fake Transcript objects
whose public attributes mirror the installed library.
"""

from src.youtube_handler import YouTubeTranscriptApi, YOUTUBE_AVAILABLE, fetch_youtube_transcript


class _FakeSnippet:
    def __init__(self, text, start, duration):
        self.text = text
        self.start = start
        self.duration = duration


class _FakeTranscript:
    def __init__(self, language_code, is_generated, is_translatable, text="hello world there"):
        self.language_code = language_code
        self.language = language_code
        self.is_generated = is_generated
        self.is_translatable = is_translatable
        self._text = text

    def fetch(self):
        # Simulate merged segments with timestamps
        return [
            _FakeSnippet(token, i, 1.0)
            for i, token in enumerate(self._text.split())
        ]

    def translate(self, target):
        return _FakeTranscript(target, self.is_generated, False, "translated " + self._text)


class _FakeList:
    def __init__(self, tracks):
        self._tracks = tracks

    def __iter__(self):
        return iter(self._tracks)


class _FakeAPI:
    def __init__(self, tracks):
        self._tracks = tracks

    def list(self, video_id):
        return _FakeList(self._tracks)

    def fetch(self, video_id):
        raise AssertionError("fallback fetch() should not be reached in these cases")


import src.youtube_handler as _mod


def _run(tracks, target="en"):
    saved_api = _mod.YouTubeTranscriptApi
    saved_avail = _mod.YOUTUBE_AVAILABLE
    _mod.YouTubeTranscriptApi = lambda: _FakeAPI(tracks)  # noqa
    _mod.YOUTUBE_AVAILABLE = True
    try:
        return fetch_youtube_transcript(
            "https://www.youtube.com/watch?v=AAAAAAAAAAA",
            target_language=target,
        )
    finally:
        _mod.YouTubeTranscriptApi = saved_api
        _mod.YOUTUBE_AVAILABLE = saved_avail


def test_prefers_manual_in_target_language():
    r = _run([
        _FakeTranscript("hi", True, True, "hindi words"),
        _FakeTranscript("en", False, True, "manual english"),
        _FakeTranscript("en", True, True, "asr english"),
    ])
    assert r["success"] is True
    assert r["language"] == "en"
    assert r["is_generated"] is False
    assert r["translated_from"] is None
    assert "manual english" in r["transcript"]


def test_falls_back_to_manual_other_language_and_translates():
    r = _run([
        _FakeTranscript("es", False, True, "espanol manual"),
    ])
    assert r["success"] is True
    assert r["language"] == "en"
    assert r["translated_from"] == "es"
    assert "translated espanol" in r["transcript"]


def test_untranslatable_hindi_returns_original_not_failure():
    r = _run([
        _FakeTranscript("hi", True, False, "hindi only words"),
    ])
    assert r["success"] is True
    assert r["language"] == "hi"
    assert r["translated_from"] is None
    assert "hindi only words" in r["transcript"]


def test_generated_english_chosen_over_translate_when_available():
    r = _run([
        _FakeTranscript("bn", True, True, "bangla words"),
        _FakeTranscript("hi", True, True, "hindi words"),
        _FakeTranscript("en", True, True, "english asr words"),
    ])
    assert r["success"] is True
    assert r["language"] == "en"
    assert r["is_generated"] is True
    assert "english asr words" in r["transcript"]


def test_segments_have_timestamps():
    r = _run([_FakeTranscript("en", False, True, "one two three")])
    assert r["segments"]
    assert r["segments"][0]["timestamp"] == "00:00"
    assert r["segments"][1]["timestamp"] == "00:01"


def test_empty_tracks_returns_clean_error():
    r = _run([])
    assert r["success"] is False
    assert "no available transcript" in r["error"]