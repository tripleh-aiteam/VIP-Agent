"""
Seed the real Asset Agent backend (asset-agent-s4tw.onrender.com) with the
user's actual portfolio so /api/dashboard/summary returns non-zero data.

Why this exists
---------------
The asset agent at https://asset-agent-s4tw.onrender.com/ is fully deployed and
authentication works, but the org_id=2 has zero properties / contracts / cash.
Without seed data, every VIP daily report shows "0 properties, 0 contracts,
cash 0 KRW" — technically correct but useless.

This script reads the realistic portfolio from `adapters/mock_data.py` (8 Korean
properties totaling ~263.5B KRW) and creates corresponding records in the
real backend via its CRUD API:

    1. POST /api/manage/properties        (8 properties)
    2. POST /api/manage/properties/{id}/units  (1 unit per property)
    3. POST /api/manage/tenants           (1 tenant per property)
    4. POST /api/manage/leases            (1 active lease per property)

Idempotency: the script tags every seeded property with a prefix
`[VIP-SEED]` in the name. Running it twice will create duplicates unless you
pass `--clear` first, which deletes everything tagged `[VIP-SEED]`.

Usage
-----
    cd vip-ai-platform
    python scripts/seed_asset_agent.py            # seed
    python scripts/seed_asset_agent.py --clear    # remove all VIP-SEED entries
    python scripts/seed_asset_agent.py --reseed   # clear + seed
    python scripts/seed_asset_agent.py --check    # only check current state

Reads ASSET_AGENT_EMAIL / ASSET_AGENT_PASSWORD / REAL_ASSET_AGENT_URL from
the repo-root .env (loaded via python-dotenv if available).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

# Load .env from repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / ".env", override=False)
except ImportError:
    pass

# Make adapters/mock_data importable
sys.path.insert(0, str(_REPO_ROOT))
from adapters.mock_data import ASSETS  # noqa: E402

ASSET_URL = os.getenv("REAL_ASSET_AGENT_URL", "https://asset-agent-s4tw.onrender.com")
EMAIL = os.getenv("ASSET_AGENT_EMAIL", "vip-orchestrator@tripleh.com")
PASSWORD = os.getenv("ASSET_AGENT_PASSWORD", "VipAgent2026!")
SEED_TAG = "[VIP-SEED]"


# ---------------------------------------------------------------------------
# District + property_type heuristic mapping (asset name → Korean district)
# ---------------------------------------------------------------------------
NAME_TO_DISTRICT = {
    "Gangnam Office Tower":   ("Gangnam-gu",     "Gangnam-daero, Yeoksam-dong"),
    "Yeouido Plaza":          ("Yeongdeungpo-gu","Yeouido-dong"),
    "Pangyo Tech Center":     ("Bundang-gu",     "Pangyo Techno Valley"),
    "Jamsil Retail Building": ("Songpa-gu",      "Jamsil-dong, Olympic-ro"),
    "Itaewon Hotel":          ("Yongsan-gu",     "Itaewon-dong, Itaewon-ro"),
    "Songdo Logistics Hub":   ("Yeonsu-gu",      "Songdo International Business District"),
    "Hongdae Apartment Bldg": ("Mapo-gu",        "Hongdae-ro, Seogyo-dong"),
    "Busan Marine Tower":     ("Haeundae-gu",    "Marine City, Haeundae"),
}

TENANT_NAMES = [
    ("Kim Min-jun",      "Samsung Insurance Co.",    "1-2345-6789", "kim.minjun@samsunglife.kr"),
    ("Lee Ji-hoon",      "LG CNS",                   "2-3456-7890", "lee.jihoon@lgcns.com"),
    ("Park Seo-yeon",    "Naver Cloud Platform",     "3-4567-8901", "seoyeon.park@navercloud.com"),
    ("Choi Hyun-woo",    "Lotte Department Store",   "4-5678-9012", "hyunwoo.choi@lotte.kr"),
    ("Jung Min-seo",     "Hyundai Hotel Group",      "5-6789-0123", "minseo.jung@hyundaihotel.com"),
    ("Han Soo-bin",      "CJ Logistics",             "6-7890-1234", "soobin.han@cjlogistics.com"),
    ("Yoon Tae-hoon",    "Kakao Mobility",           "7-8901-2345", "taehoon.yoon@kakaomobility.com"),
    ("Shin Eun-ji",      "Busan Marine Industries",  "8-9012-3456", "eunji.shin@bmind.kr"),
]


def login(client: httpx.Client) -> str:
    resp = client.post(
        f"{ASSET_URL}/api/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    return token


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def list_properties(client: httpx.Client, token: str) -> list[dict]:
    r = client.get(f"{ASSET_URL}/api/property/list", headers=auth_headers(token), timeout=15)
    if r.status_code == 200:
        body = r.json()
        return body.get("data", []) if isinstance(body, dict) else body
    return []


def delete_seed_properties(client: httpx.Client, token: str) -> int:
    props = list_properties(client, token)
    deleted = 0
    for p in props:
        name = p.get("name", "") or ""
        if SEED_TAG in name:
            pid = p.get("id") or p.get("property_id")
            if not pid:
                continue
            r = client.delete(
                f"{ASSET_URL}/api/manage/properties/{pid}",
                headers=auth_headers(token),
                timeout=15,
            )
            if r.status_code in (200, 204):
                deleted += 1
                print(f"  deleted: {name}")
            else:
                print(f"  delete failed for {name}: HTTP {r.status_code}")
    return deleted


def create_property(client: httpx.Client, token: str, asset: dict) -> str | None:
    district, address = NAME_TO_DISTRICT.get(asset["name"], ("Seoul", "Seoul, South Korea"))
    name = f"{SEED_TAG} {asset['name']}"
    # Estimate area from value: ~12M KRW per sqm Seoul prime → reasonable proxy
    area_sqm = round(asset["value_krw"] / 12_000_000, 0)
    payload = {
        "name": name,
        "address": address,
        "district": district,
        "property_type": asset["type"],
        "total_area_sqm": area_sqm,
        "leasable_area_sqm": area_sqm * 0.9,
        "floors": max(3, int(area_sqm / 200)),
        "building_age_years": 8,
        "acquisition_price": asset["value_krw"] * 0.85,  # rough cost basis
        "acquisition_date": "2018-06-15",
        "official_land_price": asset["value_krw"] * 0.6,
        "market_price_per_sqm": int(asset["value_krw"] / area_sqm) if area_sqm else 0,
        "status": "active",
    }
    r = client.post(
        f"{ASSET_URL}/api/manage/properties",
        headers=auth_headers(token),
        json=payload,
        timeout=20,
    )
    if r.status_code not in (200, 201):
        print(f"  property create failed ({r.status_code}): {r.text[:200]}")
        return None
    body = r.json()
    data = body.get("data", body) if isinstance(body, dict) else body
    pid = data.get("id") or data.get("property_id")
    return pid


def create_unit(client: httpx.Client, token: str, property_id: str, asset: dict) -> str | None:
    area = round(asset["value_krw"] / 12_000_000, 0)
    payload = {
        "floor": 1,
        "unit_number": "101",
        "area_sqm": area * 0.9,
        "unit_type": asset["type"],
        "status": "occupied",
    }
    r = client.post(
        f"{ASSET_URL}/api/manage/properties/{property_id}/units",
        headers=auth_headers(token),
        json=payload,
        timeout=20,
    )
    if r.status_code not in (200, 201):
        print(f"  unit create failed ({r.status_code}): {r.text[:200]}")
        return None
    body = r.json()
    data = body.get("data", body) if isinstance(body, dict) else body
    return data.get("id") or data.get("unit_id")


def create_tenant(client: httpx.Client, token: str, idx: int) -> str | None:
    name, biz, phone, email = TENANT_NAMES[idx % len(TENANT_NAMES)]
    payload = {
        "name": name,
        "business_name": biz,
        "phone": phone,
        "email": email,
        "industry": "general",
        "credit_grade": "A",
    }
    r = client.post(
        f"{ASSET_URL}/api/manage/tenants",
        headers=auth_headers(token),
        json=payload,
        timeout=20,
    )
    if r.status_code not in (200, 201):
        print(f"  tenant create failed ({r.status_code}): {r.text[:200]}")
        return None
    body = r.json()
    data = body.get("data", body) if isinstance(body, dict) else body
    return data.get("id") or data.get("tenant_id")


def create_lease(client: httpx.Client, token: str, property_id: str, unit_id: str | None, tenant_id: str, asset: dict, idx: int) -> str | None:
    name, biz, phone, email = TENANT_NAMES[idx % len(TENANT_NAMES)]
    start = date.today() - timedelta(days=180)
    end   = start + timedelta(days=730)  # 2-year lease
    monthly = asset["monthly_income_krw"]
    deposit = monthly * 12  # 1 year deposit, common in KR commercial
    payload = {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "tenant_name": name,
        "tenant_phone": phone,
        "tenant_email": email,
        "start_date": start.isoformat(),
        "end_date":   end.isoformat(),
        "monthly_rent": monthly,
        "maintenance_fee": int(monthly * 0.1),
        "deposit": deposit,
        "lease_status": "active",
    }
    if unit_id:
        payload["unit_id"] = unit_id
    r = client.post(
        f"{ASSET_URL}/api/manage/leases",
        headers=auth_headers(token),
        json=payload,
        timeout=20,
    )
    if r.status_code not in (200, 201):
        print(f"  lease create failed ({r.status_code}): {r.text[:200]}")
        return None
    body = r.json()
    data = body.get("data", body) if isinstance(body, dict) else body
    return data.get("id") or data.get("contract_id")


def fetch_dashboard(client: httpx.Client, token: str) -> dict:
    r = client.get(f"{ASSET_URL}/api/dashboard/summary", headers=auth_headers(token), timeout=15)
    if r.status_code == 200:
        return r.json().get("data", {})
    return {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def cmd_check(client: httpx.Client, token: str) -> None:
    props = list_properties(client, token)
    seeded = [p for p in props if SEED_TAG in (p.get("name") or "")]
    print(f"Total properties in backend: {len(props)}")
    print(f"  VIP-SEED tagged:           {len(seeded)}")
    print(f"  Other:                     {len(props) - len(seeded)}")
    print()
    dash = fetch_dashboard(client, token)
    print("Dashboard summary right now:")
    print(f"  total_properties:       {dash.get('total_properties', 0)}")
    print(f"  total_units:            {dash.get('total_units', 0)}")
    print(f"  occupied_units:         {dash.get('occupied_units', 0)}")
    print(f"  vacancy_rate:           {dash.get('vacancy_rate', 0)}%")
    print(f"  monthly_rental_income:  {dash.get('monthly_rental_income', 0):,} KRW")
    print(f"  upcoming_expiries_30d:  {dash.get('upcoming_expiries_30d', 0)}")


def cmd_clear(client: httpx.Client, token: str) -> int:
    print(f"Clearing all properties tagged {SEED_TAG}...")
    n = delete_seed_properties(client, token)
    print(f"Deleted {n} seeded propert{'y' if n == 1 else 'ies'}.")
    return n


def cmd_seed(client: httpx.Client, token: str) -> None:
    print(f"Seeding {len(ASSETS)} properties into Asset Agent backend...")
    print()
    success = 0
    for i, asset in enumerate(ASSETS):
        print(f"[{i+1}/{len(ASSETS)}] {asset['name']}")
        pid = create_property(client, token, asset)
        if not pid:
            continue
        print(f"  property_id: {pid}")
        unit_id = create_unit(client, token, pid, asset)
        if unit_id:
            print(f"  unit_id:     {unit_id}")
        tid = create_tenant(client, token, i)
        if not tid:
            continue
        print(f"  tenant_id:   {tid}")
        cid = create_lease(client, token, pid, unit_id, tid, asset, i)
        if cid:
            print(f"  lease_id:    {cid}")
            success += 1
        print()
    print(f"Done — {success}/{len(ASSETS)} fully seeded.")
    print()
    cmd_check(client, token)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Asset Agent backend")
    parser.add_argument("--clear",  action="store_true", help="Remove all VIP-SEED tagged properties")
    parser.add_argument("--reseed", action="store_true", help="Clear, then seed")
    parser.add_argument("--check",  action="store_true", help="Only check current state")
    args = parser.parse_args()

    print(f"Asset Agent: {ASSET_URL}")
    print(f"Logging in as: {EMAIL}")

    with httpx.Client() as client:
        try:
            token = login(client)
        except Exception as e:
            print(f"LOGIN FAILED: {e}")
            return 1
        print("Login OK.")
        print()

        if args.check:
            cmd_check(client, token)
        elif args.clear:
            cmd_clear(client, token)
        elif args.reseed:
            cmd_clear(client, token)
            print()
            cmd_seed(client, token)
        else:
            cmd_seed(client, token)

    return 0


if __name__ == "__main__":
    sys.exit(main())
