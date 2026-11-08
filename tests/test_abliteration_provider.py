"""abliteration.ai provider: detection, request defaults, env-var key."""
from src import llm_core as L


def test_detects_abliteration_host():
    assert L._detect_provider("https://api.abliteration.ai/v1/chat/completions") == "abliteration"
    assert L._detect_provider("https://abliteration.ai.evil.example/v1") != "abliteration"


def test_defaults_applied():
    p = {"model": "abliterated-model-large-v2", "messages": [], "temperature": 1.0}
    L._apply_provider_defaults("abliteration", p)
    assert p["top_p"] == 1
    assert p["max_tokens"] == 32768
    assert p["reasoning_effort"] == "high"
    assert p["include_reasoning"] is True
    assert p["temperature"] == 1.0


def test_caller_values_win():
    p = {"model": "m", "max_tokens": 500}
    L._apply_provider_defaults("abliteration", p, "low")
    assert p["max_tokens"] == 500 and p["reasoning_effort"] == "low"


def test_other_providers_untouched():
    p = {"model": "m"}
    L._apply_provider_defaults("openrouter", p)
    assert p == {"model": "m"}


def test_env_key_fallback(monkeypatch):
    from src.endpoint_resolver import build_headers
    monkeypatch.setenv("ABLITERATION_API_KEY", "k123")
    assert build_headers(None, "https://api.abliteration.ai/v1")["Authorization"] == "Bearer k123"
    assert build_headers("dbkey", "https://api.abliteration.ai/v1")["Authorization"] == "Bearer dbkey"
