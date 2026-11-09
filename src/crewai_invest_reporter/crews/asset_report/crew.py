from __future__ import annotations

import json
from datetime import date

from crewai import Agent, BaseLLM, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from crewai_invest_reporter.crews.asset_report.guidelines import (
    analysis_guidelines,
    asset_class_label,
)
from crewai_invest_reporter.domain.models import ResearchDossier


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1)


def asset_report_inputs(dossier: ResearchDossier, today: date | None = None) -> dict[str, str]:
    """Flatten a dossier into the prompt variables used by config/tasks.yaml."""
    today = today or date.today()
    asset = dossier.asset
    data = dossier.to_dict()
    return {
        "asset_id": asset.identifier,
        "asset_name": asset.name or asset.identifier,
        "asset_class": asset_class_label(asset.asset_class),
        "analysis_guidelines": analysis_guidelines(asset.asset_class),
        "current_year": str(today.year),
        "today": today.isoformat(),
        "official_documents": _json(
            {"documents": data["documents"], "other_recent_filings": data["other_recent_filings"]}
        ),
        "news_items": _json(data["news"]),
        "fundamentals_data": _json(data["fundamentals"]),
        "data_gaps": _json(data["data_gaps"]),
    }


@CrewBase
class AssetReportCrew:
    """Analyzes a pre-collected research dossier and writes a rated report (pt-BR).

    The agents have no tools: every source was already fetched deterministically by
    ``services.research.ResearchCollector`` and arrives through the task inputs.
    """

    agents: list[BaseAgent]
    tasks: list[Task]

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, verbose: bool = True, llm: BaseLLM | None = None) -> None:
        self.verbose = verbose
        self.llm = llm  # None: CrewAI's default LLM (OPENAI_API_KEY, MODEL)

    @agent
    def filings_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["filings_analyst"],  # type: ignore[index]
            llm=self.llm,
            verbose=self.verbose,
        )

    @agent
    def news_synthesizer(self) -> Agent:
        return Agent(
            config=self.agents_config["news_synthesizer"],  # type: ignore[index]
            llm=self.llm,
            verbose=self.verbose,
        )

    @agent
    def fundamentals_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["fundamentals_analyst"],  # type: ignore[index]
            llm=self.llm,
            verbose=self.verbose,
        )

    @agent
    def investment_rater(self) -> Agent:
        return Agent(
            config=self.agents_config["investment_rater"],  # type: ignore[index]
            llm=self.llm,
            verbose=self.verbose,
        )

    @task
    def filings_analysis_task(self) -> Task:
        return Task(config=self.tasks_config["filings_analysis_task"])  # type: ignore[index]

    @task
    def news_synthesis_task(self) -> Task:
        return Task(
            config=self.tasks_config["news_synthesis_task"],  # type: ignore[index]
            context=[self.filings_analysis_task()],
        )

    @task
    def fundamentals_task(self) -> Task:
        return Task(config=self.tasks_config["fundamentals_task"], context=[])  # type: ignore[index]

    @task
    def investment_rating_task(self) -> Task:
        return Task(
            config=self.tasks_config["investment_rating_task"],  # type: ignore[index]
            context=[
                self.filings_analysis_task(),
                self.news_synthesis_task(),
                self.fundamentals_task(),
            ],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=self.verbose,
        )
