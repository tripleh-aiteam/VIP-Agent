"""
Mock Stock Agent
Simulates an external stock market sub-agent for development/testing.
"""


def run(task: dict) -> dict:
    return {
        "agent": "mock-stock-agent",
        "status": "completed",
        "task_id": task.get("task_id"),
        "result": {"message": "Mock stock data returned"},
    }
