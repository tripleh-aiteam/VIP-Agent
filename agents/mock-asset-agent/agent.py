"""
Mock Asset Agent
Simulates an external asset management sub-agent for development/testing.
"""


def run(task: dict) -> dict:
    return {
        "agent": "mock-asset-agent",
        "status": "completed",
        "task_id": task.get("task_id"),
        "result": {"message": "Mock asset data returned"},
    }
