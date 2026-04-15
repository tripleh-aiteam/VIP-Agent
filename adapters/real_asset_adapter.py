"""
Real Asset Agent Adapter
Translates VIP Orchestrator requests to the real Asset Operations Backend API.
Handles authentication, endpoint mapping, and response normalization.
"""

import os
from typing import Any

import httpx

from adapters.base_adapter import BaseAdapter, AdapterResult


class RealAssetAdapter(BaseAdapter):
    """Adapter for the real Asset Operations Backend (asset-agent-s4tw.onrender.com)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._token: str | None = None
        self._agent_email = os.getenv("ASSET_AGENT_EMAIL", "vip-orchestrator@tripleh.com")
        self._agent_password = os.getenv("ASSET_AGENT_PASSWORD", "VipAgent2026!")

    def _login(self) -> str | None:
        """Authenticate with the Asset Agent and get a bearer token."""
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    f"{self.endpoint_url}/api/auth/login",
                    json={"email": self._agent_email, "password": self._agent_password},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self._token = data.get("access_token")
                    return self._token
        except Exception:
            pass
        return None

    def _get_token(self) -> str | None:
        """Get cached token or login."""
        if self._token:
            return self._token
        return self._login()

    def _auth_headers(self) -> dict:
        """Build auth headers with bearer token."""
        token = self._get_token()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _fetch(self, path: str) -> dict | None:
        """Fetch data from asset agent with auth. Retries login once if 401."""
        headers = self._auth_headers()
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(f"{self.endpoint_url}{path}", headers=headers)
                if resp.status_code == 401:
                    # Token expired — re-login
                    self._token = None
                    headers = self._auth_headers()
                    resp = client.get(f"{self.endpoint_url}{path}", headers=headers)
                if resp.status_code == 200:
                    return resp.json()
        except Exception:
            pass
        return None

    def execute(self, task_run_id: str, trace_id: str, task_type: str, input_payload: dict[str, Any]) -> AdapterResult:
        """Execute a task by calling real Asset Agent endpoints and merging results."""

        try:
            # Fetch data from multiple endpoints
            dashboard = self._fetch("/api/dashboard/summary")
            alerts = self._fetch("/api/dashboard/alerts")
            cash = self._fetch("/api/cash/positions")
            forecast = self._fetch("/api/cash/forecast")
            rental = self._fetch("/api/cash/rental-income/summary")
            daily_report = self._fetch("/api/scheduler/reports/daily-summary")
            vacancies = self._fetch("/api/lease/vacancies")

            if not dashboard:
                return AdapterResult(
                    success=False, status="failed", agent_id=self.agent_name,
                    error_message="Failed to connect to Asset Agent — auth or network error",
                )

            dash_data = dashboard.get("data", {})
            cash_data = (cash or {}).get("data", {})
            forecast_data = (forecast or {}).get("data", [])
            rental_data = (rental or {}).get("data", [])
            alerts_data = (alerts or {}).get("data", [])
            vacancy_data = (vacancies or {}).get("data", [])
            report_data = (daily_report or {}).get("data", {})

            # Build normalized output
            output = {
                "source": "real-asset-agent",
                "portfolio": {
                    "total_properties": dash_data.get("total_properties", 0),
                    "total_units": dash_data.get("total_units", 0),
                    "occupied_units": dash_data.get("occupied_units", 0),
                    "vacant_units": dash_data.get("vacant_units", 0),
                    "vacancy_rate": dash_data.get("vacancy_rate", 0),
                    "monthly_rental_income": dash_data.get("monthly_rental_income", 0),
                    "total_overdue": dash_data.get("total_overdue_amount", 0),
                    "upcoming_expiries_30d": dash_data.get("upcoming_expiries_30d", 0),
                    "upcoming_expiries_90d": dash_data.get("upcoming_expiries_90d", 0),
                    "pending_approvals": dash_data.get("pending_approvals", 0),
                },
                "cash": {
                    "total_balance": cash_data.get("total_balance", 0),
                    "currency": cash_data.get("currency", "KRW"),
                    "accounts": len(cash_data.get("accounts", [])),
                },
                "forecast": [
                    {"month": f.get("month"), "net_cashflow": f.get("net_cashflow", 0)}
                    for f in forecast_data[:3]
                ],
                "rental_income": [
                    {"month": r.get("month"), "collection_rate": r.get("collection_rate", 0), "overdue": r.get("overdue", 0)}
                    for r in rental_data[:3]
                ],
                "alerts_count": len(alerts_data),
                "vacancies_count": len(vacancy_data),
                "daily_report": report_data.get("data", {}).get("portfolio", {}) if isinstance(report_data.get("data"), dict) else {},
            }

            # Build formatted executive report
            props = dash_data.get("total_properties", 0)
            units = dash_data.get("total_units", 0)
            occupied = dash_data.get("occupied_units", 0)
            vacant = dash_data.get("vacant_units", 0)
            vacancy_rate = dash_data.get("vacancy_rate", 0)
            income = dash_data.get("monthly_rental_income", 0)
            overdue = dash_data.get("total_overdue_amount", 0)
            exp_30 = dash_data.get("upcoming_expiries_30d", 0)
            exp_90 = dash_data.get("upcoming_expiries_90d", 0)
            pending = dash_data.get("pending_approvals", 0)
            balance = cash_data.get("total_balance", 0)
            currency = cash_data.get("currency", "KRW")

            # Occupancy rate
            occ_rate = round((occupied / units * 100), 1) if units > 0 else 0

            # Forecast net
            forecast_net = sum(f.get("net_cashflow", 0) for f in forecast_data[:3])

            # Risk assessment
            risks = []
            if vacancy_rate > 10:
                risks.append(f"High vacancy rate ({vacancy_rate}%)")
            if overdue > 0:
                risks.append(f"Overdue payments: {overdue:,.0f} {currency}")
            if exp_30 > 0:
                risks.append(f"{exp_30} lease(s) expiring within 30 days")
            if pending > 0:
                risks.append(f"{pending} pending approval(s)")
            risk_level = "High" if len(risks) >= 3 else "Medium" if len(risks) >= 1 else "Low"

            # Collection rate
            latest_collection = rental_data[0].get("collection_rate", 100) if rental_data else 100

            report_lines = [
                "━━━ Asset Portfolio Report ━━━",
                "",
                f"Properties: {props} properties, {units} units",
                f"Occupancy: {occ_rate}% ({occupied} occupied, {vacant} vacant)",
                f"Vacancy Rate: {vacancy_rate}%",
                "",
                "━━━ Financial Summary ━━━",
                "",
                f"Monthly Rental Income: {income:,.0f} {currency}",
                f"Overdue Amount: {overdue:,.0f} {currency}",
                f"Collection Rate: {latest_collection}%",
                f"Cash Balance: {balance:,.0f} {currency}",
                f"3-Month Forecast: {forecast_net:,.0f} {currency} net",
                "",
                "━━━ Lease Status ━━━",
                "",
                f"Expiring (30 days): {exp_30} lease(s)",
                f"Expiring (90 days): {exp_90} lease(s)",
                f"Pending Approvals: {pending}",
                "",
                "━━━ Alerts ━━━",
                "",
                f"Active Alerts: {len(alerts_data)}",
                f"Vacant Units: {len(vacancy_data)}",
                "",
                "━━━ Risk Assessment ━━━",
                "",
                f"Risk Level: {risk_level}",
            ]
            if risks:
                for r in risks:
                    report_lines.append(f"  • {r}")
            else:
                report_lines.append("  All metrics within normal range.")

            summary = "\n".join(report_lines)
            output["report_text"] = summary
            output["risk_level"] = risk_level
            output["risk_factors"] = risks

            return AdapterResult(
                success=True,
                status="completed",
                agent_id=self.agent_name,
                summary=summary,
                output_payload=output,
            )

        except Exception as e:
            return AdapterResult(
                success=False, status="failed", agent_id=self.agent_name,
                error_message=f"Real asset adapter error: {e}",
            )

    def health_check(self) -> dict:
        """Check real asset agent health."""
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{self.endpoint_url}/health")
                if resp.status_code == 200:
                    data = resp.json()
                    return {"reachable": True, "authenticated": self._get_token() is not None, **data}
            return {"reachable": False}
        except Exception as e:
            return {"reachable": False, "error": str(e)}
