import json
from app.services import agent


def test_openai_compatible_adapter_parses_json_and_keeps_results_separate(monkeypatch):
    monkeypatch.setattr(agent, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(agent, "LLM_BASE_URL", "https://provider.example/v1")
    monkeypatch.setattr(agent, "LLM_MODEL", "example-model")
    seen = {}
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            return json.dumps({"choices": [{"message": {"content": '```json\\n{"summary":"Review the measured evidence.","hypotheses":["Supplier variation is a hypothesis."],"recommendations":["Stratify by supplier."],"risks":["Sample may be limited."]}\\n```'}}]}).encode()
    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        seen["body"] = json.loads(request.data.decode())
        seen["timeout"] = timeout
        return FakeResponse()
    monkeypatch.setattr(agent.urllib.request, "urlopen", fake_urlopen)
    result = agent._llm_interpret({"verified_findings": [{"text": "n=10"}]}, "Review")
    assert result["provider_status"] == "configured_response"
    assert result["summary"] == "Review the measured evidence."
    assert result["hypotheses"] == ["Supplier variation is a hypothesis."]
    assert seen["url"] == "https://provider.example/v1/chat/completions"
    assert seen["authorization"] == "Bearer test-key"
    assert seen["body"]["model"] == "example-model"
    assert "raw_rows" not in json.dumps(seen["body"])
