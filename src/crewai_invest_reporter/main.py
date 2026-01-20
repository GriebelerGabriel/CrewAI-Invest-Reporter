#!/usr/bin/env python
import sys
import warnings
from datetime import datetime

from dotenv import load_dotenv

from crewai_invest_reporter.crew import InvestReporter

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

load_dotenv()

# This main file is intended to be a way for you to run your
# crew locally, so refrain from adding unnecessary logic into this file.
# Replace with inputs you want to test with, it will automatically
# interpolate any tasks and agents information


def run():
    """
    Run the crew.
    """
    ticker = sys.argv[1] if len(sys.argv) > 1 else "PETR4"
    inputs = {"ticker": ticker, "current_year": str(datetime.now().year)}

    try:
        InvestReporter().crew().kickoff(inputs=inputs)
    except Exception as e:
        raise Exception(f"An error occurred while running the crew: {e}") from e


def train():
    """
    Train the crew for a given number of iterations.
    """
    ticker = sys.argv[3] if len(sys.argv) > 3 else "PETR4"
    inputs = {"ticker": ticker, "current_year": str(datetime.now().year)}
    try:
        InvestReporter().crew().train(
            n_iterations=int(sys.argv[1]), filename=sys.argv[2], inputs=inputs
        )

    except Exception as e:
        raise Exception(f"An error occurred while training the crew: {e}") from e


def replay():
    """
    Replay the crew execution from a specific task.
    """
    try:
        InvestReporter().crew().replay(task_id=sys.argv[1])

    except Exception as e:
        raise Exception(f"An error occurred while replaying the crew: {e}") from e


def test():
    """
    Test the crew execution and returns the results.
    """
    ticker = sys.argv[3] if len(sys.argv) > 3 else "PETR4"
    inputs = {"ticker": ticker, "current_year": str(datetime.now().year)}

    try:
        InvestReporter().crew().test(
            n_iterations=int(sys.argv[1]), eval_llm=sys.argv[2], inputs=inputs
        )

    except Exception as e:
        raise Exception(f"An error occurred while testing the crew: {e}") from e


def run_with_trigger():
    """
    Run the crew with trigger payload.
    """
    import json

    if len(sys.argv) < 2:
        raise Exception("No trigger payload provided. Please provide JSON payload as argument.")

    try:
        trigger_payload = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        raise Exception("Invalid JSON payload provided as argument") from None

    inputs = {"crewai_trigger_payload": trigger_payload, "ticker": "", "current_year": ""}

    try:
        result = InvestReporter().crew().kickoff(inputs=inputs)
        return result
    except Exception as e:
        raise Exception(f"An error occurred while running the crew with trigger: {e}") from e


if __name__ == "__main__":
    run()
