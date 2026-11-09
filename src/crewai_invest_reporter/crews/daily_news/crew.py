from __future__ import annotations

import re
from datetime import date

from crewai import Agent, BaseLLM, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from crewai_invest_reporter.crews import strip_code_fence, to_json
from crewai_invest_reporter.domain.models import ResearchDossier

NOTHING_NEW = "sem novidades relevantes"
# "[2]" or "[1, 4]" at the end of a bullet: the numbers of the items it is based on.
_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def item_urls(dossier: ResearchDossier) -> list[str]:
    """URL of each numbered item, in prompt order: CVM documents first, then news."""
    return [d.filing.url for d in dossier.documents] + [n.url for n in dossier.news]


def daily_news_inputs(dossier: ResearchDossier, today: date, days: int) -> dict[str, str]:
    data = dossier.to_dict()
    items = [{"tipo": "documento CVM", **doc} for doc in data["documents"]]
    items += [{"tipo": "notícia", **news} for news in data["news"]]
    for number, item in enumerate(items, start=1):
        item.pop("url", None)  # cited by number; long links would only cost tokens
        item["n"] = number
    stock = dossier.stock
    return {
        "ticker": stock.ticker,
        "company": stock.name or stock.ticker,
        "today": today.isoformat(),
        "days": str(days),
        "items": to_json(items),
    }


def renumber_citations(text: str, urls: list[str]) -> tuple[str, list[str]]:
    """Renumber the agent's citations 1, 2... in order of first use, dropping invalid ones.

    Returns the new text and the cited URLs, in the new numbering order.
    """
    cited: list[str] = []

    def renumber(match: re.Match[str]) -> str:
        numbers = []
        for part in match.group(1).split(","):
            index = int(part) - 1
            if 0 <= index < len(urls):
                if urls[index] not in cited:
                    cited.append(urls[index])
                numbers.append(str(cited.index(urls[index]) + 1))
        return f"[{', '.join(dict.fromkeys(numbers))}]" if numbers else ""

    text = _CITATION.sub(renumber, text)
    return "\n".join(line.rstrip() for line in text.splitlines()), cited


@CrewBase
class DailyNewsCrew:
    """One agent turning a stock's new news and filings into a short pt-BR briefing."""

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, verbose: bool = False, llm: BaseLLM | None = None) -> None:
        self.verbose = verbose
        self.llm = llm  # None: CrewAI's default LLM (OPENAI_API_KEY, MODEL)

    @agent
    def news_writer(self) -> Agent:
        return Agent(
            config=self.agents_config["news_writer"],  # type: ignore[index]
            llm=self.llm,
            verbose=self.verbose,
        )

    @task
    def daily_summary_task(self) -> Task:
        return Task(config=self.tasks_config["daily_summary_task"])  # type: ignore[index]

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents, tasks=self.tasks, process=Process.sequential, verbose=self.verbose
        )


def summarize(
    dossier: ResearchDossier, today: date, days: int, llm: BaseLLM | None = None
) -> tuple[str, list[str]] | None:
    """The briefing and the URLs it cites, or None when the agent found nothing relevant."""
    inputs = daily_news_inputs(dossier, today, days)
    text = strip_code_fence(DailyNewsCrew(llm=llm).crew().kickoff(inputs=inputs).raw).strip()
    if text.lower().rstrip(".") == NOTHING_NEW:
        return None
    return renumber_citations(text, item_urls(dossier))
