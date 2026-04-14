# VIP AI Platform — Entity Relationship Diagram

## Overview

15 tables across 6 domains. PostgreSQL is the system of record.
All writes go through the Orchestrator API — gateway/OpenClaw never writes directly.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            CORE DOMAIN                                 │
│                                                                         │
│  ┌──────────────────┐       ┌──────────────────┐                       │
│  │   core_agents    │       │  core_channels   │                       │
│  ├──────────────────┤       ├──────────────────┤                       │
│  │ id          (PK) │       │ id          (PK) │                       │
│  │ name             │       │ type             │                       │
│  │ type             │       │ config_json      │                       │
│  │ version          │       │ status           │                       │
│  │ owner_team       │       └────────┬─────────┘                       │
│  │ endpoint_url     │                │                                  │
│  │ auth_type        │       ┌────────┴─────────┐                       │
│  │ status           │       │  core_sessions   │                       │
│  │ is_mock          │       ├──────────────────┤                       │
│  │ capabilities_json│       │ id          (PK) │                       │
│  │ created_at       │       │ user_id          │                       │
│  │ updated_at       │       │ channel_id  (FK) │──→ core_channels.id   │
│  └──┬───┬───────────┘       │ org_id           │                       │
│     │   │                   │ session_key      │                       │
│     │   │                   │ context_json     │                       │
│     │   │                   └──────────────────┘                       │
└─────│───│──────────────────────────────────────────────────────────────┘
      │   │
      │   │
┌─────│───│──────────────────────────────────────────────────────────────┐
│     │   │              ORCHESTRATION DOMAIN                            │
│     │   │                                                              │
│     │   │   ┌─────────────────────────┐                                │
│     │   │   │  orch_task_definitions  │                                │
│     │   │   ├─────────────────────────┤                                │
│     │   │   │ id               (PK)  │                                │
│     │   │   │ task_type              │                                │
│     │   │   │ target_agent_type      │                                │
│     │   │   │ input_schema_json      │                                │
│     │   │   │ output_schema_json     │                                │
│     │   │   │ timeout_seconds        │                                │
│     │   │   │ requires_judgement     │                                │
│     │   │   └───────┬────────────────┘                                │
│     │   │           │                                                  │
│     │   │   ┌───────┴────────────────┐    ┌──────────────────────┐    │
│     │   │   │    orch_task_runs      │    │  orch_schedule_rules │    │
│     │   │   ├────────────────────────┤    ├──────────────────────┤    │
│     │   │   │ id              (PK)  │    │ id              (PK) │    │
│     │   └──→│ target_agent_id (FK)  │    │ name                 │    │
│     │       │ task_definition_id(FK)│    │ cron_expr            │    │
│     │       │ initiator_type       │    │ target_task_def_id(FK)│──→ orch_task_definitions.id
│     │       │ initiator_id         │    │ enabled              │    │
│     │       │ source_channel       │    └──────────────────────┘    │
│     │       │ trace_id             │                                │
│     │       │ input_payload        │    ┌──────────────────────┐    │
│     │       │ output_payload       │    │    orch_reports      │    │
│     │       │ status               │    ├──────────────────────┤    │
│     │       │ error_message        │    │ id              (PK) │    │
│     │       │ started_at           │    │ report_type          │    │
│     │       │ finished_at          │    │ source_run_ids_json  │    │
│     │       └───────┬──────────────┘    │ content_json         │    │
│     │               │                   │ delivery_channel     │    │
│     │               │                   └──────────────────────┘    │
└─────│───────────────│──────────────────────────────────────────────┘
      │               │
      │               │
┌─────│───────────────│──────────────────────────────────────────────┐
│     │               │          AUDIT DOMAIN                        │
│     │               │                                              │
│     │       ┌───────┴─────────────────┐                            │
│     │       │ audit_judgement_cases   │                            │
│     │       ├─────────────────────────┤                            │
│     │       │ id              (PK)   │                            │
│     │       │ task_run_id     (FK)   │──→ orch_task_runs.id       │
│     │       │ rule_result            │                            │
│     │       │ model_result           │    ┌────────────────────┐  │
│     │       │ risk_score             │    │ audit_event_logs   │  │
│     │       │ decision               │    ├────────────────────┤  │
│     │       │ evidence_json          │    │ id          (PK)  │  │
│     │       └───────┬────────────────┘    │ source            │  │
│     │               │                     │ event_type        │  │
│     │       ┌───────┴────────────────┐    │ trace_id          │  │
│     │       │audit_approval_requests│    │ payload_json      │  │
│     │       ├────────────────────────┤    └────────────────────┘  │
│     │       │ id              (PK)  │                            │
│     │       │ judgement_case_id(FK) │──→ audit_judgement_cases.id│
│     │       │ requested_by         │                            │
│     │       │ approved_by          │                            │
│     │       │ decision             │                            │
│     │       │ decided_at           │                            │
│     │       └────────────────────────┘                            │
└─────│────────────────────────────────────────────────────────────┘
      │
┌─────│────────────────────────────────────────────────────────────┐
│     │                 A2A DOMAIN                                  │
│     │                                                            │
│     │       ┌────────────────────────┐                            │
│     │       │    a2a_messages        │                            │
│     │       ├────────────────────────┤                            │
│     │       │ id              (PK)  │                            │
│     ├──────→│ sender_agent_id (FK)  │──→ core_agents.id          │
│     └──────→│ target_agent_id (FK)  │──→ core_agents.id          │
│             │ task_run_id     (FK)  │──→ orch_task_runs.id       │
│             │ trace_id             │                            │
│             │ message_type         │                            │
│             │ envelope_json        │                            │
│             │ status               │                            │
│             └────────────────────────┘                            │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                 AGENT-OPS DOMAIN                                  │
│                                                                    │
│  ┌────────────────────────┐                                        │
│  │   agent_heartbeats     │                                        │
│  ├────────────────────────┤                                        │
│  │ id              (PK)  │                                        │
│  │ agent_id        (FK)  │──→ core_agents.id                      │
│  │ status                │                                        │
│  │ latency_ms            │                                        │
│  │ metadata_json         │                                        │
│  └────────────────────────┘                                        │
│                                                                    │
│  ┌──────────────────────────────────┐                              │
│  │ realty_spatial_capture_sessions  │                              │
│  ├──────────────────────────────────┤                              │
│  │ id                      (PK)   │                              │
│  │ agent_id                (FK)   │──→ core_agents.id            │
│  │ device_id                      │                              │
│  │ property_ref                   │                              │
│  │ video_uri / audio_uri          │                              │
│  │ model_3d_uri                   │                              │
│  │ metadata_json                  │                              │
│  │ processing_status              │                              │
│  └──────────────────────────────────┘                              │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│                 TELEGRAM DOMAIN                                    │
│                                                                    │
│  ┌──────────────────┐     ┌──────────────────────┐                │
│  │ telegram_users   │     │  telegram_actions     │                │
│  ├──────────────────┤     ├──────────────────────┤                │
│  │ id          (PK) │     │ id              (PK) │                │
│  │ telegram_user_id │     │ telegram_user_id     │                │
│  │ linked_user_id   │     │ action_type          │                │
│  │ role             │     │ related_task_run_id(FK)│──→ orch_task_runs.id
│  │ status           │     │ payload_json         │                │
│  └──────────────────┘     │ status               │                │
│                           └──────────────────────┘                │
└──────────────────────────────────────────────────────────────────┘
```

## Table Summary

| Domain | Table | Rows (seed) | Description |
|--------|-------|-------------|-------------|
| Core | `core_agents` | 3 | Registered agents (mock + live) |
| Core | `core_channels` | 5 | Communication channels |
| Core | `core_sessions` | 0 | User sessions per channel |
| Orchestration | `orch_task_definitions` | 3 | Task type definitions |
| Orchestration | `orch_task_runs` | 0 | Task execution records |
| Orchestration | `orch_schedule_rules` | 0 | Cron-based scheduling |
| Orchestration | `orch_reports` | 0 | Generated reports |
| Audit | `audit_judgement_cases` | 0 | Judgement decisions |
| Audit | `audit_approval_requests` | 0 | Human approval flow |
| Audit | `audit_event_logs` | 0 | System event trail |
| A2A | `a2a_messages` | 0 | Agent-to-agent messages |
| Agent-Ops | `agent_heartbeats` | 0 | Liveness monitoring |
| Spatial | `realty_spatial_capture_sessions` | 0 | AI Glasses / 3D capture |
| Telegram | `telegram_users` | 1 | Linked Telegram accounts |
| Telegram | `telegram_actions` | 0 | Telegram command log |

## Foreign Key Map

| From | Column | To |
|------|--------|----|
| `core_sessions` | `channel_id` | `core_channels.id` |
| `orch_task_runs` | `task_definition_id` | `orch_task_definitions.id` |
| `orch_task_runs` | `target_agent_id` | `core_agents.id` |
| `orch_schedule_rules` | `target_task_definition_id` | `orch_task_definitions.id` |
| `audit_judgement_cases` | `task_run_id` | `orch_task_runs.id` |
| `audit_approval_requests` | `judgement_case_id` | `audit_judgement_cases.id` |
| `a2a_messages` | `sender_agent_id` | `core_agents.id` |
| `a2a_messages` | `target_agent_id` | `core_agents.id` |
| `a2a_messages` | `task_run_id` | `orch_task_runs.id` |
| `agent_heartbeats` | `agent_id` | `core_agents.id` |
| `realty_spatial_capture_sessions` | `agent_id` | `core_agents.id` |
| `telegram_actions` | `related_task_run_id` | `orch_task_runs.id` |
