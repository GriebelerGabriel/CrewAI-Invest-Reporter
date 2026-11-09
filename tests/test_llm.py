from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from crewai_invest_reporter import main
from crewai_invest_reporter.bootstrap import Container
from crewai_invest_reporter.domain.errors import ConfigurationError
from crewai_invest_reporter.settings import LlmSettings, Settings

VK = "sk-bf-test"


def _container(**llm) -> Container:
    return Container(Settings(llm=LlmSettings(**llm)))


def test_without_bifrost_crewai_defaults_are_kept():
    container = _container(model="gpt-4.1-mini")
    container.settings.llm.validate()
    assert container.llm is None


@pytest.mark.parametrize(
    "model", ["openai/gpt-4.1-mini", "anthropic/claude-sonnet-4-5", "gemini/gemini-2.5-flash"]
)
def test_bifrost_llm_keeps_provider_prefixed_model(model):
    llm = _container(model=model, bifrost_base_url="http://gw:8080/v1", bifrost_api_key=VK).llm
    assert llm.model == model
    assert llm.base_url == "http://gw:8080/v1"
    assert llm.api_key == VK
    assert llm.default_headers == {"x-bf-vk": VK}


def test_bifrost_defaults_to_crewai_default_model():
    container = _container(bifrost_base_url="http://gw:8080/v1", bifrost_api_key=VK)
    assert container.llm.model == "openai/gpt-4.1-mini"


@pytest.mark.parametrize(
    ("llm", "message"),
    [
        ({"bifrost_base_url": "http://gw:8080/v1"}, "BIFROST_API_KEY is missing"),
        (
            {"bifrost_base_url": "http://gw:8080/v1", "bifrost_api_key": VK, "model": "gpt-4o"},
            "provider/model",
        ),
    ],
)
def test_invalid_bifrost_settings_fail_clearly(llm, message):
    with pytest.raises(ConfigurationError, match=message):
        _ = _container(**llm).llm


def test_settings_read_bifrost_env(monkeypatch):
    monkeypatch.setenv("BIFROST_BASE_URL", " http://10.0.0.5:8080/v1/ ")
    monkeypatch.setenv("BIFROST_API_KEY", VK)
    monkeypatch.setenv("MODEL", "anthropic/claude-sonnet-4-5")
    assert Settings.from_env().llm == LlmSettings(
        "anthropic/claude-sonnet-4-5", "http://10.0.0.5:8080/v1", VK
    )


def test_asset_command_fails_before_collecting_when_key_is_missing(monkeypatch, capsys):
    monkeypatch.setenv("BIFROST_BASE_URL", "http://gw:8080/v1")
    monkeypatch.setenv("BIFROST_API_KEY", "")
    assert main.main(["asset", "PETR4"]) == 1
    assert "BIFROST_API_KEY is missing" in capsys.readouterr().err


def test_crew_gives_every_agent_the_gateway_llm():
    from crewai_invest_reporter.crews.asset_report.crew import AssetReportCrew

    llm = _container(bifrost_base_url="http://gw:8080/v1", bifrost_api_key=VK).llm
    crew = AssetReportCrew(verbose=False, llm=llm).crew()
    assert all(agent.llm is llm for agent in crew.agents)


@pytest.fixture
def fake_gateway():
    """A local OpenAI-compatible endpoint that records the requests it receives."""
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append({"path": self.path, "headers": dict(self.headers), "body": body})
            reply = json.dumps(
                {
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "olá"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1", received
    server.shutdown()


def test_calls_reach_the_gateway_with_model_and_virtual_key(fake_gateway):
    base_url, received = fake_gateway
    llm = _container(
        model="anthropic/claude-sonnet-4-5", bifrost_base_url=base_url, bifrost_api_key=VK
    ).llm
    assert llm.call("oi") == "olá"
    (request,) = received
    headers = {k.lower(): v for k, v in request["headers"].items()}
    assert request["path"] == "/v1/chat/completions"
    assert request["body"]["model"] == "anthropic/claude-sonnet-4-5"
    assert headers["authorization"] == f"Bearer {VK}"
    assert headers["x-bf-vk"] == VK
