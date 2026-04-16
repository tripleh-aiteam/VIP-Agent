"""
VIP AI Platform — Seed Data
Populates mock agents, default channels, and sample task definitions.
Run: python -m db.seed
"""

import uuid
from datetime import datetime
from db.base import engine, SessionLocal, Base
from db.models import (
    CoreAgent, CoreChannel, OrchTaskDefinition, OrchScheduleRule, TelegramUser,
)


MOCK_AGENTS = [
    {
        "name": "Asset Agent",
        "type": "asset",
        "version": "0.1.0",
        "owner_team": "vip-core",
        "endpoint_url": "http://localhost:9010",
        "auth_type": "none",
        "status": "active",
        "is_mock": True,
        "capabilities_json": {"actions": ["fetch_summary", "evaluate_portfolio"]},
        "supported_task_types": ["asset_summary"],
        "supported_channels": ["web", "telegram"],
        "priority_score": 100,
        "reliability_score": 1.0,
        "description": "Mock asset management agent for development",
    },
    {
        "name": "Stock Agent",
        "type": "stock",
        "version": "0.1.0",
        "owner_team": "vip-core",
        "endpoint_url": "http://localhost:9011",
        "auth_type": "none",
        "status": "active",
        "is_mock": True,
        "capabilities_json": {"actions": ["fetch_market_data", "analyze_trends"]},
        "supported_task_types": ["stock_analysis"],
        "supported_channels": ["web", "telegram"],
        "priority_score": 100,
        "reliability_score": 1.0,
        "description": "Mock stock market agent for development",
    },
    {
        "name": "Real Estate Agent",
        "type": "realty",
        "version": "0.1.0",
        "owner_team": "vip-core",
        "endpoint_url": "http://localhost:9012",
        "auth_type": "none",
        "status": "active",
        "is_mock": True,
        "capabilities_json": {"actions": ["fetch_listings", "spatial_capture"]},
        "supported_task_types": ["realty_listing_fetch"],
        "supported_channels": ["web", "telegram", "ai_glass"],
        "priority_score": 100,
        "reliability_score": 1.0,
        "description": "Mock real estate agent with AI Glasses support",
    },
]

DEFAULT_CHANNELS = [
    {"type": "web", "config_json": {"origin": "http://localhost:3000"}, "status": "active"},
    {"type": "telegram", "config_json": {"bot_token_env": "TELEGRAM_BOT_TOKEN"}, "status": "active"},
    {"type": "slack", "config_json": {}, "status": "inactive"},
    {"type": "whatsapp", "config_json": {}, "status": "inactive"},
    {"type": "ai_glass", "config_json": {}, "status": "planned"},
]

TASK_DEFINITIONS = [
    {
        "task_type": "asset_summary",
        "target_agent_type": "asset",
        "input_schema_json": {"type": "object", "properties": {"portfolio_id": {"type": "string"}}},
        "output_schema_json": {"type": "object", "properties": {"summary": {"type": "object"}}},
        "timeout_seconds": 120,
        "requires_judgement": False,
    },
    {
        "task_type": "stock_analysis",
        "target_agent_type": "stock",
        "input_schema_json": {"type": "object", "properties": {"symbols": {"type": "array"}}},
        "output_schema_json": {"type": "object", "properties": {"analysis": {"type": "object"}}},
        "timeout_seconds": 180,
        "requires_judgement": True,
    },
    {
        "task_type": "realty_listing_fetch",
        "target_agent_type": "realty",
        "input_schema_json": {"type": "object", "properties": {"region": {"type": "string"}}},
        "output_schema_json": {"type": "object", "properties": {"listings": {"type": "array"}}},
        "timeout_seconds": 300,
        "requires_judgement": False,
    },
]


def run_seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        # Seed agents
        for agent_data in MOCK_AGENTS:
            exists = db.query(CoreAgent).filter_by(name=agent_data["name"]).first()
            if not exists:
                db.add(CoreAgent(**agent_data))
                print(f"  + Agent: {agent_data['name']}")
            else:
                print(f"  = Agent exists: {agent_data['name']}")

        # Seed channels
        for ch_data in DEFAULT_CHANNELS:
            exists = db.query(CoreChannel).filter_by(type=ch_data["type"]).first()
            if not exists:
                db.add(CoreChannel(**ch_data))
                print(f"  + Channel: {ch_data['type']}")
            else:
                print(f"  = Channel exists: {ch_data['type']}")

        # Seed task definitions
        for td_data in TASK_DEFINITIONS:
            exists = db.query(OrchTaskDefinition).filter_by(task_type=td_data["task_type"]).first()
            if not exists:
                db.add(OrchTaskDefinition(**td_data))
                print(f"  + TaskDef: {td_data['task_type']}")
            else:
                print(f"  = TaskDef exists: {td_data['task_type']}")

        # Seed schedule rules
        SCHEDULE_RULES = [
            {"name": "asset_summary_morning", "cron_expr": "0 9 * * *", "task_type": "asset_summary"},
            {"name": "asset_summary_evening", "cron_expr": "0 18 * * *", "task_type": "asset_summary"},
            {"name": "stock_analysis_hourly", "cron_expr": "0 * * * *", "task_type": "stock_analysis"},
            {"name": "realty_listing_daily", "cron_expr": "0 10 * * *", "task_type": "realty_listing_fetch"},
            {"name": "weekly_summary_friday", "cron_expr": "0 17 * * 5", "task_type": "asset_summary"},
        ]
        for sr_data in SCHEDULE_RULES:
            exists = db.query(OrchScheduleRule).filter_by(name=sr_data["name"]).first()
            if not exists:
                td = db.query(OrchTaskDefinition).filter_by(task_type=sr_data["task_type"]).first()
                if td:
                    db.add(OrchScheduleRule(
                        name=sr_data["name"],
                        cron_expr=sr_data["cron_expr"],
                        target_task_definition_id=td.id,
                        enabled=True,
                    ))
                    print(f"  + Schedule: {sr_data['name']} ({sr_data['cron_expr']})")
            else:
                print(f"  = Schedule exists: {sr_data['name']}")

        # Seed default telegram admin
        exists = db.query(TelegramUser).filter_by(telegram_user_id="admin_000").first()
        if not exists:
            db.add(TelegramUser(
                telegram_user_id="admin_000",
                linked_user_id="system",
                role="admin",
                status="active",
            ))
            print("  + TelegramUser: admin_000")
        else:
            print("  = TelegramUser exists: admin_000")

        db.commit()
        print("\nSeed completed successfully.")

    except Exception as e:
        db.rollback()
        print(f"\nSeed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_seed()
