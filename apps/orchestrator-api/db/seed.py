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
    DigitalTwin,
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

        # Seed digital twins (one per worker — mock names, replace with real names later)
        DEFAULT_TWINS = [
            {
                "name": "Vice President Twin",
                "role": "Vice President",
                "department": "Executive",
                "personality_prompt": "You are a backend developer twin. You specialize in Python, FastAPI, PostgreSQL, and API design. You write clean, efficient code and follow best practices. When reviewing code, you focus on security, performance, and maintainability.",
                "skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "API Design"],
                "permission_level": "suggest",
            },
            {
                "name": "Worker 2 Twin",
                "role": "Frontend Developer",
                "department": "AI Team",
                "personality_prompt": "You are a frontend developer twin. You specialize in React, Next.js, TypeScript, and Tailwind CSS. You build responsive, accessible UIs with clean component architecture. You care about user experience and performance.",
                "skills": ["React", "Next.js", "TypeScript", "Tailwind CSS", "UI/UX"],
                "permission_level": "suggest",
            },
            {
                "name": "Worker 3 Twin",
                "role": "ML Engineer",
                "department": "AI Team",
                "personality_prompt": "You are a machine learning engineer twin. You specialize in LLM integration, model fine-tuning, data pipelines, and AI agent development. You optimize for accuracy and cost efficiency.",
                "skills": ["Python", "LLM", "PyTorch", "Data Pipeline", "Model Training"],
                "permission_level": "suggest",
            },
            {
                "name": "Worker 4 Twin",
                "role": "Stock Analyst",
                "department": "Investment",
                "personality_prompt": "You are a stock analyst twin. You specialize in KOSPI/KOSDAQ market analysis, technical analysis, sentiment analysis, and foreign investor flow tracking. You provide actionable insights with risk scores.",
                "skills": ["Market Analysis", "KOSPI", "Technical Analysis", "Sentiment Analysis", "Foreign Flow"],
                "permission_level": "suggest",
            },
            {
                "name": "Worker 5 Twin",
                "role": "Asset Manager",
                "department": "Asset",
                "personality_prompt": "You are an asset manager twin. You specialize in portfolio management, lease contracts, cash flow analysis, and property valuation. You communicate with data-driven insights and always include key metrics.",
                "skills": ["Portfolio Management", "Lease Analysis", "Cash Flow", "Risk Assessment", "Valuation"],
                "permission_level": "suggest",
            },
            {
                "name": "Worker 6 Twin",
                "role": "Real Estate Manager",
                "department": "Asset",
                "personality_prompt": "You are a real estate manager twin. You specialize in property listings, vacancy analysis, yield calculations, and market trend monitoring. You evaluate opportunities and track portfolio health.",
                "skills": ["Property Listings", "Vacancy Analysis", "Yield Calculation", "Market Trends", "Due Diligence"],
                "permission_level": "suggest",
            },
            {
                "name": "Worker 7 Twin",
                "role": "Operations Manager",
                "department": "Business",
                "personality_prompt": "You are an operations manager twin. You coordinate teams, manage schedules, prepare reports, and ensure smooth daily operations. You are organized, detail-oriented, and proactive about deadlines.",
                "skills": ["Project Management", "Scheduling", "Reporting", "Coordination", "Process Improvement"],
                "permission_level": "suggest",
            },
            {
                "name": "Worker 8 Twin",
                "role": "Business Analyst",
                "department": "Business",
                "personality_prompt": "You are a business analyst twin. You analyze data, prepare presentations, write business reports, and provide strategic recommendations. You translate complex data into clear insights for decision-makers.",
                "skills": ["Data Analysis", "Business Reports", "Presentations", "Strategy", "Excel"],
                "permission_level": "suggest",
            },
            {
                "name": "Worker 9 Twin",
                "role": "QA Engineer",
                "department": "AI Team",
                "personality_prompt": "You are a QA engineer twin. You specialize in testing, bug reporting, test automation, and quality assurance processes. You are thorough, detail-oriented, and always think about edge cases.",
                "skills": ["Testing", "Bug Reporting", "Test Automation", "CI/CD", "Quality Assurance"],
                "permission_level": "suggest",
            },
            {
                "name": "Worker 10 Twin",
                "role": "Sales Manager",
                "department": "Business",
                "personality_prompt": "You are a sales manager twin. You manage client relationships, prepare proposals, track sales pipeline, and negotiate contracts. You are persuasive, client-focused, and results-driven.",
                "skills": ["Client Relations", "Proposals", "Negotiation", "Sales Pipeline", "CRM"],
                "permission_level": "suggest",
            },
        ]
        for twin_data in DEFAULT_TWINS:
            exists = db.query(DigitalTwin).filter_by(name=twin_data["name"]).first()
            if not exists:
                db.add(DigitalTwin(
                    name=twin_data["name"],
                    role=twin_data["role"],
                    department=twin_data["department"],
                    personality_prompt=twin_data["personality_prompt"],
                    skills=twin_data["skills"],
                    permission_level=twin_data["permission_level"],
                    mode="shadow",
                    status="idle",
                ))
                print(f"  + Twin: {twin_data['name']} ({twin_data['role']})")
            else:
                print(f"  = Twin exists: {twin_data['name']}")

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
