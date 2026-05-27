"""
ingest_local.py
===============

One-off CLI for pushing a local file into the assistant knowledge base
WITHOUT going through the HTTP upload endpoint. Useful for:
  - First-time bootstrap (load a workbook the boss already has on disk)
  - Bulk-loading a folder of PDFs
  - Re-indexing after a schema change

Usage (from apps/orchestrator-api/):
    python -m scripts.ingest_local <path_to_file> --agent vip [--by boss@email]
    python -m scripts.ingest_local "C:/path/to/foo.xlsx" --agent vip

Requires the same env vars as the running orchestrator:
    DATABASE_URL        — points to Supabase (use the .env.supabase one)
    OPENAI_API_KEY      — for text-embedding-3-small calls
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest a local file into the assistant KB.")
    ap.add_argument("path", help="Path to xlsx/pdf/docx/pptx/csv/txt/md/json")
    ap.add_argument("--agent", default="vip", help="agent_id scope (default: vip)")
    ap.add_argument("--by", default="cli@local", help="uploaded_by tag")
    args = ap.parse_args()

    src = pathlib.Path(args.path)
    if not src.exists():
        print(f"[!] File not found: {src}", file=sys.stderr)
        return 2
    blob = src.read_bytes()
    print(f"→ Reading {src.name} ({len(blob):,} bytes) for agent={args.agent!r}")

    # Make the parent package importable without `pip install -e .`
    here = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(here))

    # Load .env / .env.supabase. The repo root is two levels above this script
    # (apps/orchestrator-api/scripts/ingest_local.py → vip-ai-platform/).
    # .env.supabase takes precedence for DATABASE_URL so we hit the live DB.
    try:
        from dotenv import load_dotenv
        repo_root = here.parent.parent
        env_supabase = repo_root / ".env.supabase"
        if env_supabase.exists():
            load_dotenv(env_supabase, override=True)
            print(f"  ✓ loaded {env_supabase}")
        env_local = repo_root / ".env"
        if env_local.exists():
            load_dotenv(env_local, override=False)
            print(f"  ✓ loaded {env_local}")
    except ImportError:
        print("  ! python-dotenv not installed — relying on shell env", file=sys.stderr)

    if not os.getenv("DATABASE_URL"):
        print("[!] DATABASE_URL not set. Did you load .env.supabase?", file=sys.stderr)
        return 3
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")):
        print("[!] OPENAI_API_KEY not set. Embeddings will fail.", file=sys.stderr)
        return 4

    from db.base import SessionLocal
    from services.knowledge_ingest import ingest_file

    db = SessionLocal()
    try:
        result = ingest_file(
            db,
            agent_id=args.agent,
            filename=src.name,
            mime_type=None,
            blob=blob,
            uploaded_by=args.by,
        )
        print(f"✓ Indexed {result['chunk_count']} chunks (file_id={result['file_id']}, status={result['status']})")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
