"""LLM endpoint probes in Service Health (src/service_health.py)."""
import urllib.error

from src.service_health import (collect_probes, llm_probe_request, llm_probes, model_refs,
                                probe_llm, update_health)

SAFE = lambda url: (True, "ok")  # noqa: E731


def status(code, seen=None):
    def get(url, headers, timeout):
        if seen is not None:
            seen.append((url, dict(headers)))
        return code
    return get


def test_probe_request_shapes():
    assert llm_probe_request("http://localhost:8002/v1/") == ("http://localhost:8002/v1/models", {})
    url, h = llm_probe_request("https://api.openai.com/v1/chat/completions", "sk-1")
    assert url == "https://api.openai.com/v1/models" and h == {"Authorization": "Bearer sk-1"}
    url, h = llm_probe_request("https://api.anthropic.com", "ak")
    assert url == "https://api.anthropic.com/v1/models"
    assert h["x-api-key"] == "ak" and "Authorization" not in h and h["anthropic-version"]
    assert llm_probe_request("https://api.anthropic.com/v1")[0] == "https://api.anthropic.com/v1/models"
    assert llm_probe_request("http://localhost:11434/api")[0] == "http://localhost:11434/api/tags"
    assert llm_probe_request("https://ollama.com", "k")[0] == "https://ollama.com/api/tags"
    # Look-alike host is not treated as Anthropic.
    url, h = llm_probe_request("https://anthropic.com.evil.test/v1", "k")
    assert "x-api-key" not in h and h["Authorization"] == "Bearer k"


def test_probe_llm_status_classes():
    assert probe_llm("k", "M", "http://h/v1", http_get_headers=status(200), url_check=SAFE)["ok"]
    assert probe_llm("k", "M", "http://h/v1", http_get_headers=status(404), url_check=SAFE)["ok"]
    assert probe_llm("k", "M", "http://h/v1", http_get_headers=status(429), url_check=SAFE)["ok"]
    r = probe_llm("k", "M", "http://h/v1", "sk-secret", http_get_headers=status(401), url_check=SAFE)
    assert r["ok"] is False and "API key rejected" in r["detail"] and "API key" in r["fix"]
    assert "sk-secret" not in r["detail"] + r["fix"]
    r = probe_llm("k", "M", "http://h/v1", "", http_get_headers=status(403), url_check=SAFE)
    assert r["ok"] is False and "needs an API key" in r["detail"]
    r = probe_llm("k", "M", "http://h/v1", http_get_headers=status(503), url_check=SAFE)
    assert r["ok"] is False and "HTTP 503" in r["detail"]

    def boom(u, h, t):
        raise urllib.error.URLError(ConnectionRefusedError())
    r = probe_llm("k", "M", "http://u:pw@h/v1?x=1", http_get_headers=boom, url_check=SAFE)
    assert r["ok"] is False and "connection refused" in r["detail"] and "pw" not in r["detail"]


def test_probe_llm_unsafe_url_is_not_fetched():
    seen = []
    r = probe_llm("k", "M", "http://169.254.169.254/v1", "sk",
                  http_get_headers=status(200, seen), url_check=lambda u: (False, "link-local blocked"))
    assert r["ok"] is False and "link-local" in r["detail"] and not seen


def test_model_refs_reads_roles_and_fallbacks():
    s = {"default_endpoint_id": "a", "utility_endpoint_id": "", "task_endpoint_id": "b",
         "default_model_fallbacks": [{"endpoint_id": "c", "model": "m"}, {"model": "x"}, "junk"],
         "utility_model_fallbacks": "not-a-list"}
    assert model_refs(s.get) == [("Chat", "a"), ("Task", "b"), ("Chat fallback", "c")]


EPS = [
    {"id": "a", "name": "Local vLLM", "base_url": "http://gpu:8002/v1", "enabled": True},
    {"id": "b", "name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
     "api_key": "or-key", "enabled": True},
    {"id": "off", "name": "Old", "base_url": "http://old/v1", "enabled": False},
    {"id": "img", "name": "Images", "base_url": "http://img/v1", "model_type": "image", "enabled": True},
]


def test_llm_probes_dedupes_and_reports_broken_refs():
    seen = []
    refs = [("Chat", "a"), ("Utility", "a"), ("Task", "b"), ("Research", "off"),
            ("Chat fallback", "gone")]
    out = llm_probes(EPS, refs, http_get_headers=status(200, seen), url_check=SAFE)
    by_key = {r["key"]: r for r in out}
    assert by_key["llm:a"]["label"] == "Model Local vLLM (Chat, Utility)" and by_key["llm:a"]["ok"]
    assert by_key["llm:b"]["ok"]
    assert "disabled" in by_key["model-ref:off"]["detail"] and by_key["model-ref:off"]["ok"] is False
    assert "no longer exists" in by_key["model-ref:gone"]["detail"]
    assert "AI Defaults" in by_key["model-ref:gone"]["fix"]
    assert len(seen) == 2  # one call per endpoint, image endpoint never probed
    assert ("https://openrouter.ai/api/v1/models", {"Authorization": "Bearer or-key"}) in seen
    assert all("old" not in u and "img" not in u for u, _ in seen)


def test_llm_probes_without_roles_probes_enabled_llm_endpoints_capped():
    seen = []
    out = llm_probes(EPS, [], http_get_headers=status(200, seen), url_check=SAFE)
    assert [r["key"] for r in out] == ["llm:a", "llm:b"]
    many = [{"id": str(i), "name": f"E{i}", "base_url": f"http://e{i}/v1"} for i in range(10)]
    assert len(llm_probes(many, [], http_get_headers=status(200), url_check=SAFE, limit=3)) == 3


def test_collect_probes_includes_llm_only_when_endpoints_given(tmp_path):
    base = dict(settings={"search_provider": "brave", "brave_api_key": "x"}, url_check=SAFE)
    assert not any(r["key"].startswith("llm:") for r in collect_probes(**base))
    res = collect_probes(**base, model_endpoints=EPS, model_refs=[("Chat", "b")],
                         http_get_headers=status(401))
    llm = [r for r in res if r["key"].startswith("llm:")]
    assert len(llm) == 1 and llm[0]["ok"] is False and "rejected" in llm[0]["detail"]

    # Folds into the usual down → recovered flow.
    path = str(tmp_path / "s.json")
    assert update_health("o", res, now=0, state_path=path) == []
    ev = update_health("o", res, now=60, state_path=path)
    assert [e["kind"] for e in ev if e["key"] == "llm:b"] == ["down"]
    ok = collect_probes(**base, model_endpoints=EPS, model_refs=[("Chat", "b")],
                        http_get_headers=status(200))
    ev = update_health("o", ok, now=120, state_path=path)
    assert [e["kind"] for e in ev if e["key"] == "llm:b"] == ["recovered"]
