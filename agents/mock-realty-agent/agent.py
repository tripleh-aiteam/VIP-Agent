"""
Mock Realty Agent
Simulates an external real estate sub-agent for development/testing.
"""


def run(task: dict) -> dict:
    return {
        "agent": "mock-realty-agent",
        "status": "completed",
        "task_id": task.get("task_id"),
        "result": {"message": "Mock realty data returned"},
    }
