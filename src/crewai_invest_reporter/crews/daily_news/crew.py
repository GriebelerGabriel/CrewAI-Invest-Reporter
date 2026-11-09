from __future__ import annotations

import json
from datetime import date

from crewai import Agent, BaseLLM, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from crewai_invest_reporter.domain.models import ResearchDossier
from crewai_invest_reporter.services.report_writer import strip_code_fence

NOTHING_NEW = "sem novidades relevantes"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1)


def _price_line(price: float | None, change: float | None) -> str:
    if price is None:
        return "não disponível"
    line = f"R$ {price:.2f}"
    return f"{line} ({change:+.2%} desde o resumo anterior)" if change is not None else line


def daily_news_inputs(
    dossier: ResearchDossier, today: date, days: int, price_change: float | None
) -> dict[str, str]:
    stock = dossier.stock
    data = dossier.to_dict()
    price = dossier.fundamentals.metrics.get("currentPrice") if dossier.fundamentals else None
    return {
        "ticker": stock.ticker,
        "company": stock.name or stock.ticker,
        "today": today.isoformat(),
        "days": str(days),
        "price_line": _price_line(price, price_change),
        "news_items": _json(data["news"]),
        "new_documents": _json(data["documents"]),
    }


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
    dossier: ResearchDossier,
    today: date,
    days: int,
    price_change: float | None,
    llm: BaseLLM | None = None,
) -> str | None:
    """The briefing, or None when the agent found nothing relevant."""
    inputs = daily_news_inputs(dossier, today, days, price_change)
    text = strip_code_fence(DailyNewsCrew(llm=llm).crew().kickoff(inputs=inputs).raw).strip()
    return None if text.lower().rstrip(".") == NOTHING_NEW else text
