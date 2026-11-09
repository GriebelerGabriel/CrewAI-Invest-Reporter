from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal

from crewai import Agent, BaseLLM, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task
from pydantic import BaseModel, Field

from crewai_invest_reporter.crews import strip_code_fence, to_json
from crewai_invest_reporter.domain.models import Analysis, ResearchDossier, Verdict

logger = logging.getLogger(__name__)


class VerdictRecord(BaseModel):
    """What the last task extracts from the report (CrewAI validates the LLM's JSON with it)."""

    rating: Literal["Excellent to invest", "Good to invest", "Neutral / Watch", "Bad to invest"]
    probability_good: int = Field(ge=0, le=100)
    probability_loss: int = Field(ge=0, le=100)
    confidence: Literal["alta", "média", "baixa"]


def asset_report_inputs(dossier: ResearchDossier, today: date) -> dict[str, str]:
    """Flatten a dossier into the prompt variables used by config/tasks.yaml."""
    stock = dossier.stock
    data = dossier.to_dict()
    return {
        "ticker": stock.ticker,
        "company": stock.name or stock.ticker,
        "current_year": str(today.year),
        "today": today.isoformat(),
        "official_documents": to_json(
            {"documents": data["documents"], "other_recent_filings": data["other_recent_filings"]}
        ),
        "news_items": to_json(data["news"]),
        "fundamentals_data": to_json(data["fundamentals"]),
        "data_gaps": to_json(data["data_gaps"]),
    }


@CrewBase
class AssetReportCrew:
    """Analyzes a pre-collected research dossier on one stock and writes a rated report (pt-BR).

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

    @task
    def verdict_task(self) -> Task:
        return Task(
            config=self.tasks_config["verdict_task"],  # type: ignore[index]
            context=[self.investment_rating_task()],
            output_pydantic=VerdictRecord,
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=self.verbose,
        )


def analyze(dossier: ResearchDossier, today: date, llm: BaseLLM | None = None) -> Analysis:
    """Run the crew on a dossier: the rating task's markdown report plus the parsed verdict."""
    result = AssetReportCrew(llm=llm).crew().kickoff(inputs=asset_report_inputs(dossier, today))
    return read_analysis(result.tasks_output)


def read_analysis(tasks_output: list[Any]) -> Analysis:
    by_name = {output.name: output for output in tasks_output}
    report = strip_code_fence(by_name["investment_rating_task"].raw)
    record = getattr(by_name.get("verdict_task"), "pydantic", None)
    if not isinstance(record, VerdictRecord):
        logger.warning("The verdict could not be parsed; the report is kept without it")
        return Analysis(report)
    return Analysis(report, Verdict(**record.model_dump()))
