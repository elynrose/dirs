from unittest.mock import MagicMock, patch

from director_api.config import Settings
from director_api.services import research_service


def test_search_web_uses_wikipedia_when_no_tavily():
    settings = Settings.model_construct(tavily_api_key=None, research_http_timeout_sec=12.0)
    fake_json = [
        "q",
        ["Battle of Jericho", "Book of Joshua"],
        ["Ancient city", "Biblical figure"],
        [
            "https://en.wikipedia.org/wiki/Battle_of_Jericho",
            "https://en.wikipedia.org/wiki/Book_of_Joshua",
        ],
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_json
    mock_resp.raise_for_status = MagicMock()
    with patch("director_api.services.research_service.httpx.Client") as client_cls:
        inst = MagicMock()
        inst.__enter__.return_value = inst
        inst.get.return_value = mock_resp
        client_cls.return_value = inst
        hits = research_service.search_web("Joshua walls of Jericho", settings, 3)
    assert len(hits) == 2
    assert hits[0]["url"].startswith("https://en.wikipedia.org/wiki/")
    assert "Jericho" in hits[0]["title"] or "Jericho" in hits[0]["url"]
