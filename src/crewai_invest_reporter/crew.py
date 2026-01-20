from crewai import Agent, Crew, Process, Task
from crewai.agents.agent_builder.base_agent import BaseAgent
from crewai.project import CrewBase, agent, crew, task

from crewai_invest_reporter.tools.news_search_tool import NewsSearchTool
from crewai_invest_reporter.tools.stock_fundamentals_tool import StockFundamentalsTool


@CrewBase
class InvestReporter:
    """InvestReporter crew"""

    agents: list[BaseAgent]
    tasks: list[Task]

    # Learn more about YAML configuration files here:
    # Agents: https://docs.crewai.com/concepts/agents#yaml-configuration-recommended
    # Tasks: https://docs.crewai.com/concepts/tasks#yaml-configuration-recommended

    @agent
    def news_researcher(self) -> Agent:
        return Agent(
            config=self.agents_config["news_researcher"],  # type: ignore[index]
            verbose=True,
            tools=[NewsSearchTool()],
        )

    @agent
    def news_synthesizer(self) -> Agent:
        return Agent(
            config=self.agents_config["news_synthesizer"],  # type: ignore[index]
            verbose=True,
        )

    @agent
    def fundamentals_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["fundamentals_analyst"],  # type: ignore[index]
            verbose=True,
            tools=[StockFundamentalsTool()],
        )

    @agent
    def investment_rater(self) -> Agent:
        return Agent(
            config=self.agents_config["investment_rater"],  # type: ignore[index]
            verbose=True,
        )

    # To learn more about structured task outputs,
    # task dependencies, and task callbacks, check out the documentation:
    # https://docs.crewai.com/concepts/tasks#overview-of-a-task
    @task
    def news_collection_task(self) -> Task:
        return Task(
            config=self.tasks_config["news_collection_task"],  # type: ignore[index]
        )

    @task
    def news_synthesis_task(self) -> Task:
        return Task(
            config=self.tasks_config["news_synthesis_task"],  # type: ignore[index]
        )

    @task
    def fundamentals_task(self) -> Task:
        return Task(
            config=self.tasks_config["fundamentals_task"],  # type: ignore[index]
        )

    @task
    def investment_rating_task(self) -> Task:
        return Task(
            config=self.tasks_config["investment_rating_task"],  # type: ignore[index]
            output_file="reports/{ticker}_investment_report.md",
        )

    @crew
    def crew(self) -> Crew:
        """Creates the InvestReporter crew"""
        # To learn how to add knowledge sources to your crew, check out the documentation:
        # https://docs.crewai.com/concepts/knowledge#what-is-knowledge

        return Crew(
            agents=self.agents,  # Automatically created by the @agent decorator
            tasks=self.tasks,  # Automatically created by the @task decorator
            process=Process.sequential,
            verbose=True,
            # process=Process.hierarchical, # In case you wanna use that instead https://docs.crewai.com/how-to/Hierarchical/
        )
