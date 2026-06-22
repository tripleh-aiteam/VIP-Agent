"""
VIP AI Platform — Report Router
POST /reports/compose/daily, /reports/compose/weekly, /reports/compose/alert
GET /reports/{id}, GET /reports/{id}/markdown
"""

import os
from uuid import UUID
from pydantic import BaseModel, Field
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from db.base import get_db
from db.models import OrchReport
from services import report_service
from services.api_security import rate_limit_compose

router = APIRouter(prefix="/reports", tags=["reports"])


class ComposeBody(BaseModel):
    delivery_channel: str = Field(default="web")
    trace_id: str = Field(default="system")
    hours_back: int = Field(default=24, ge=1, le=720, description="How many hours back to look for data")


@router.post("/compose/auto-daily", dependencies=[Depends(rate_limit_compose)])
def trigger_auto_daily(db: Session = Depends(get_db)):
    """Manually trigger the auto daily report pipeline (3 agent reports + combined)."""
    from services.scheduler_service import _auto_daily_reports
    import threading
    threading.Thread(target=_auto_daily_reports, daemon=True).start()
    return {"triggered": True, "message": "Auto daily reports running in background. Check Reports page in ~30 seconds."}


def _allowed_report_recipients() -> set[str]:
    """Allowlist of addresses the manual trigger may email. Built from
    REPORT_ALLOWED_RECIPIENTS (comma-separated) plus the configured server-side
    recipients — so an attacker cannot exfiltrate a report to an arbitrary inbox
    or abuse our SMTP to send mail to third parties."""
    allowed = {a.strip().lower() for a in (os.getenv("REPORT_ALLOWED_RECIPIENTS") or "").split(",") if a.strip()}
    for ev in ("KIWOOM_REPORT_EMAIL", "NEWSPAPER_REPORT_EMAIL", "YOUTUBE_REPORT_EMAIL",
               "MASTER_REPORT_EMAIL", "REPORT_EMAIL_TO", "SMTP_USER", "SMTP_EMAIL"):
        v = os.getenv(ev)
        if v:
            allowed.add(v.strip().lower())
    # The configured distribution list (REPORT_RECIPIENTS / DEFAULT_RECIPIENTS) is
    # allowlisted — these are the intended recipients, so a ?email test may target
    # one of them. This does NOT permit arbitrary third-party addresses.
    try:
        from services.report_email import default_recipients, EXTRA_ALLOWED_RECIPIENTS
        for r in default_recipients():
            allowed.add(r.strip().lower())
        # On-demand-only recipients (sendable via ?email=, not on the daily list).
        for r in EXTRA_ALLOWED_RECIPIENTS:
            allowed.add(r.strip().lower())
    except Exception:
        pass
    return allowed


def _resolve_recipients(email: str):
    """Validate the ?email= param, which may be a single address or a
    comma-separated list. Every address must be on the allowlist. Returns a
    single string (one address) or a list (several) for email_override, or
    raises HTTPException(403) if any address is not allowed."""
    addrs = [e.strip() for e in (email or "").split(",") if e.strip()]
    allowed = _allowed_report_recipients()
    for a in addrs:
        if a.lower() not in allowed:
            raise HTTPException(403, f"recipient not allowed: {a} — add it to REPORT_ALLOWED_RECIPIENTS env or EXTRA_ALLOWED_RECIPIENTS")
    if not addrs:
        raise HTTPException(400, "no valid recipient in ?email=")
    return addrs[0] if len(addrs) == 1 else addrs


@router.post("/compose/kiwoom", dependencies=[Depends(rate_limit_compose)])
def trigger_kiwoom_report(email: Optional[str] = Query(None, description="Optional recipient for the .docx email — must be on the REPORT_ALLOWED_RECIPIENTS allowlist (or a configured server recipient). Scheduled run uses KIWOOM_REPORT_EMAIL env."), send_all: bool = Query(False, description="Email the report to ALL configured recipients (dropdown 'generate & send')."), lang: str = Query("ko", description="Report language: 'ko' (default, Korean only) or 'en' (English)."), db: Session = Depends(get_db)):
    """Manually trigger the Kiwoom daily market report (also runs 6:30 AM KST).
    Pass ?email=<addr> (or a comma-separated list) to send the Word attachment
    to ALLOWLISTED address(es)."""
    if email:
        email = _resolve_recipients(email)
    elif send_all:
        email = "*ALL*"
    lang = "en" if (lang or "ko").strip().lower() == "en" else "ko"
    from services.scheduler_service import _kiwoom_daily_report
    import threading
    threading.Thread(target=lambda: _kiwoom_daily_report(email_override=email, lang=lang), daemon=True).start()
    return {"triggered": True, "email": email or "(env KIWOOM_REPORT_EMAIL)",
            "message": "Kiwoom daily report running in background. Check Reports → Kiwoom in ~30s."}


@router.post("/compose/newspaper", dependencies=[Depends(rate_limit_compose)])
def trigger_newspaper_report(email: Optional[str] = Query(None, description="Optional recipient for the .docx email — must be on the allowlist. Scheduled run uses NEWSPAPER_REPORT_EMAIL env."), send_all: bool = Query(False, description="Email the report to ALL configured recipients (dropdown 'generate & send')."), lang: str = Query("ko", description="Report language: 'ko' (default, Korean only) or 'en' (English)."), db: Session = Depends(get_db)):
    """Manually trigger the Newspaper (news analysis) report (also runs 7:00 AM KST).
    ?email= may be a single address or a comma-separated allowlisted list."""
    if email:
        email = _resolve_recipients(email)
    elif send_all:
        email = "*ALL*"
    lang = "en" if (lang or "ko").strip().lower() == "en" else "ko"
    from services.scheduler_service import _newspaper_daily_report
    import threading
    threading.Thread(target=lambda: _newspaper_daily_report(email_override=email, lang=lang), daemon=True).start()
    return {"triggered": True, "email": email or "(env NEWSPAPER_REPORT_EMAIL)",
            "message": "Newspaper report running in background. Check Reports → Newspaper in ~40s."}


@router.post("/compose/youtube", dependencies=[Depends(rate_limit_compose)])
def trigger_youtube_report(email: Optional[str] = Query(None, description="Optional recipient for the .docx email — must be on the allowlist. Scheduled run uses YOUTUBE_REPORT_EMAIL env."), send_all: bool = Query(False, description="Email the report to ALL configured recipients (dropdown 'generate & send')."), lang: str = Query("ko", description="Report language: 'ko' (default, Korean only) or 'en' (English)."), db: Session = Depends(get_db)):
    """Manually trigger the YouTube (video analysis) report (also runs 6:30 AM KST)."""
    if email:
        if email.strip().lower() not in _allowed_report_recipients():
            raise HTTPException(403, "recipient not allowed — add it to REPORT_ALLOWED_RECIPIENTS env")
        email = email.strip()
    elif send_all:
        email = "*ALL*"
    lang = "en" if (lang or "ko").strip().lower() == "en" else "ko"
    from services.scheduler_service import _youtube_daily_report
    import threading
    threading.Thread(target=lambda: _youtube_daily_report(email_override=email, lang=lang), daemon=True).start()
    return {"triggered": True, "email": email or "(env YOUTUBE_REPORT_EMAIL)",
            "message": "YouTube report running in background. Check Reports → YouTube in ~60s."}


@router.post("/compose/master", dependencies=[Depends(rate_limit_compose)])
def trigger_master_report(email: Optional[str] = Query(None, description="Optional recipient for the .docx email — must be on the allowlist. Scheduled run uses MASTER_REPORT_EMAIL env."), send_all: bool = Query(False, description="Email the report to ALL configured recipients (dropdown 'generate & send')."), lang: str = Query("ko", description="Report language: 'ko' (default, Korean only) or 'en' (English)."), db: Session = Depends(get_db)):
    """Manually trigger the Master synthesis report (consolidates the latest
    Kiwoom + Newspaper + YouTube reports). Also runs 6:50 AM KST."""
    if email:
        if email.strip().lower() not in _allowed_report_recipients():
            raise HTTPException(403, "recipient not allowed — add it to REPORT_ALLOWED_RECIPIENTS env")
        email = email.strip()
    elif send_all:
        email = "*ALL*"
    lang = "en" if (lang or "ko").strip().lower() == "en" else "ko"
    from services.scheduler_service import _master_daily_report
    import threading
    threading.Thread(target=lambda: _master_daily_report(email_override=email, lang=lang), daemon=True).start()
    return {"triggered": True, "email": email or "(env MASTER_REPORT_EMAIL)",
            "message": "Master report running in background. Check Reports in ~40s."}


@router.post("/compose/asset", dependencies=[Depends(rate_limit_compose)])
def trigger_asset_report(email: Optional[str] = Query(None, description="Optional recipient for the .docx email — must be on the allowlist. Scheduled run uses ASSET_REPORT_EMAIL env."), send_all: bool = Query(False, description="Email the report to ALL configured recipients."), lang: str = Query("ko", description="Report language: 'ko' (default) or 'en'."), db: Session = Depends(get_db)):
    """Manually trigger the DETAILED Asset Agent report (also runs 6:30 AM KST and
    is bundled into the 6:50 consolidated email)."""
    if email:
        if email.strip().lower() not in _allowed_report_recipients():
            raise HTTPException(403, "recipient not allowed — add it to REPORT_ALLOWED_RECIPIENTS env")
        email = email.strip()
    elif send_all:
        email = "*ALL*"
    lang = "en" if (lang or "ko").strip().lower() == "en" else "ko"
    from services.scheduler_service import _asset_daily_report
    import threading
    threading.Thread(target=lambda: _asset_daily_report(email_override=email, lang=lang), daemon=True).start()
    return {"triggered": True, "email": email or "(env ASSET_REPORT_EMAIL)",
            "message": "Detailed Asset report running in background. Check Reports in ~1-2 min."}


@router.post("/compose/breaking", dependencies=[Depends(rate_limit_compose)])
def trigger_breaking_report(
    email: Optional[str] = Query(None, description="Recipient for the .docx email — must be on the allowlist."),
    send_all: bool = Query(False, description="Email to ALL configured recipients."),
    focus: Optional[str] = Query(None, description="Optional focus event, e.g. 'Canada submarine contract Korea defense shipbuilding'."),
    urls: Optional[str] = Query(None, description="Optional comma-separated seed article URLs to read in full."),
    db: Session = Depends(get_db),
):
    """Manually trigger the 🚨 Breaking market-impact report (news → affected KR
    stocks with direction/강도/예상밴드/신뢰도)."""
    if email:
        if email.strip().lower() not in _allowed_report_recipients():
            raise HTTPException(403, "recipient not allowed — add it to REPORT_ALLOWED_RECIPIENTS env")
        email = email.strip()
    elif send_all:
        email = "*ALL*"
    seed = [u.strip() for u in (urls or "").split(",") if u.strip()] or None
    from services.scheduler_service import _breaking_report
    import threading
    threading.Thread(target=lambda: _breaking_report(email_override=email, focus=focus, seed_urls=seed), daemon=True).start()
    return {"triggered": True, "email": email or "(env BREAKING_REPORT_EMAIL)", "focus": focus,
            "message": "Breaking market-impact report running in background (~2-4 min)."}


@router.post("/compose/realty", dependencies=[Depends(rate_limit_compose)])
def trigger_realty_report(email: Optional[str] = Query(None, description="Optional recipient for the .docx email — must be on the allowlist. Scheduled run uses REALTY_REPORT_EMAIL env."), send_all: bool = Query(False, description="Email the report to ALL configured recipients."), lang: str = Query("ko", description="Report language: 'ko' (default) or 'en'."), db: Session = Depends(get_db)):
    """Manually trigger the DETAILED Real Estate Agent report (also runs ~7:05 AM KST,
    standalone email to all recipients, KO+EN)."""
    if email:
        if email.strip().lower() not in _allowed_report_recipients():
            raise HTTPException(403, "recipient not allowed — add it to REPORT_ALLOWED_RECIPIENTS env")
        email = email.strip()
    elif send_all:
        email = "*ALL*"
    lang = "en" if (lang or "ko").strip().lower() == "en" else "ko"
    from services.scheduler_service import _realty_daily_report
    import threading
    threading.Thread(target=lambda: _realty_daily_report(email_override=email, lang=lang), daemon=True).start()
    return {"triggered": True, "email": email or "(env REALTY_REPORT_EMAIL)",
            "message": "Detailed Real Estate report running in background. Check Reports in ~1-2 min."}


@router.post("/compose/all", dependencies=[Depends(rate_limit_compose)])
def trigger_all_reports(email: Optional[str] = Query(None, description="Optional single recipient (allowlisted) for a test; omit to email the full recipient list."), lang: str = Query("ko", description="Report language: 'ko' (default, Korean only) or 'en' (English)."), db: Session = Depends(get_db)):
    """On-demand 'Generate Now': build ALL 4 reports with the freshest data and
    email the consolidated set to every recipient. Runs in the background
    (~8-12 min). Used by the Reports page button."""
    if email:
        if email.strip().lower() not in _allowed_report_recipients():
            raise HTTPException(403, "recipient not allowed — add it to REPORT_ALLOWED_RECIPIENTS env")
        email = email.strip()
    lang = "en" if (lang or "ko").strip().lower() == "en" else "ko"
    from services.scheduler_service import run_all_reports_now
    import threading
    threading.Thread(target=lambda: run_all_reports_now(email_override=email, lang=lang), daemon=True).start()
    return {"triggered": True,
            "email": email or "(all recipients)",
            "message": "Generating all 4 reports with current data — the consolidated email "
                       "will arrive in ~8-12 minutes."}


@router.post("/compose/agent", dependencies=[Depends(rate_limit_compose)])
def trigger_agent_report(type: str = Query(..., description="Agent type: asset | stock | realty"), db: Session = Depends(get_db)):
    """Generate ONE agent's report on demand (saved to the dashboard + Telegram)."""
    atype = (type or "").strip().lower()
    if atype not in ("asset", "stock", "realty"):
        raise HTTPException(400, "type must be asset, stock, or realty")
    from services.scheduler_service import _single_agent_report
    import threading
    threading.Thread(target=lambda: _single_agent_report(atype), daemon=True).start()
    return {"triggered": True, "agent": atype,
            "message": f"{atype} agent report generating — check Reports → Agents in ~30-60s."}


@router.post("/capture-hourly", dependencies=[Depends(rate_limit_compose)])
def trigger_capture_hourly(db: Session = Depends(get_db)):
    """Manually capture one hourly snapshot 'part' (newspaper + youtube + kiwoom)
    right now — same as the automatic :05 hourly job. Saved, NOT emailed."""
    from services.hourly_capture import capture_hourly
    import threading
    threading.Thread(target=capture_hourly, daemon=True).start()
    return {"triggered": True, "message": "Hourly snapshot captured in background (saved, not emailed)."}


def _safe_msg(text: str) -> str:
    """Extract ONLY the non-secret error/status message from an upstream auth
    body — never the token itself or the raw body."""
    import json as _json
    try:
        j = _json.loads(text or "{}")
        for k in ("return_msg", "error_description", "msg1", "msg", "error_code", "return_code", "rt_cd"):
            if k in j:
                return str({k: j[k] for k in j if k in ("return_msg", "return_code",
                           "error_description", "error_code", "msg1", "msg", "rt_cd")})[:200]
    except Exception:
        pass
    return "(non-JSON / empty)"


@router.get("/kiwoom-short-check")
def kiwoom_short_check():
    """Diagnostic for Kiwoom 공매도: env presence + token/data STATUS + sanitized
    error message only (never returns tokens or raw bodies)."""
    import os as _os, httpx as _hx
    from datetime import datetime as _dt, timedelta as _td
    out = {"key_present": bool(_os.getenv("KIWOOM_APP_KEY")),
           "secret_present": bool(_os.getenv("KIWOOM_APP_SECRET"))}
    # Try real first, then mock — Kiwoom 8030 = 실전/모의 env mismatch, so a key
    # for the other environment still works once we hit the matching base.
    bases = ["https://api.kiwoom.com", "https://mockapi.kiwoom.com"]
    try:
        tok = None
        for base in bases:
            tr = _hx.post(f"{base}/oauth2/token",
                          json={"grant_type": "client_credentials",
                                "appkey": _os.getenv("KIWOOM_APP_KEY", ""),
                                "secretkey": _os.getenv("KIWOOM_APP_SECRET", "")}, timeout=15)
            out["token_status"] = tr.status_code
            out["token_msg"] = _safe_msg(tr.text)
            tj = tr.json() if tr.headers.get("content-type", "").startswith("application/json") else {}
            tok = tj.get("token") or tj.get("access_token")
            if tok:
                out["env"] = "real" if base == bases[0] else "mock"
                break
            # Only fall through to the next base on an 8030 env-mismatch.
            if "8030" not in (out.get("token_msg") or ""):
                break
        if tok:
            base = bases[0] if out.get("env") == "real" else bases[1]
            d2 = _dt.utcnow().strftime("%Y%m%d")
            d1 = (_dt.utcnow() - _td(days=10)).strftime("%Y%m%d")
            dr = _hx.post(f"{base}/api/dostk/shsa",
                          headers={"authorization": f"Bearer {tok}", "api-id": "ka10014",
                                   "content-type": "application/json;charset=UTF-8",
                                   "cont-yn": "N", "next-key": ""},
                          json={"stk_cd": "005930", "tm_tp": "1", "strt_dt": d1, "end_dt": d2}, timeout=20)
            out["data_status"] = dr.status_code
            out["data_msg"] = _safe_msg(dr.text)
            try:
                dj = dr.json()
                out["data_has_rows"] = any(isinstance(v, list) and v for v in dj.values())
            except Exception:
                pass
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


@router.get("/kis-deriv-check")
def kis_deriv_check():
    """Verbose diagnostic for KIS: env presence, raw token call, and one raw
    futures quotation — shows the actual KIS response for debugging."""
    import os as _os, httpx as _hx
    # Resolve via the client so a KIS_APP_SECRET_B64 override is reflected here too.
    try:
        from services.kis_client import _creds as _kis_creds
        _k, _s = _kis_creds()
        _k, _s = _k or "", _s or ""
    except Exception:
        _k = _os.getenv("KIS_APP_KEY", "") or ""
        _s = _os.getenv("KIS_APP_SECRET", "") or ""
    out = {"key_present": bool(_k), "secret_present": bool(_s),
           "b64_override": bool((_os.getenv("KIS_APP_SECRET_B64") or "").strip()),
           # Lengths only (NOT the values) — a real KIS AppKey is ~36 chars and the
           # AppSecret is ~180 chars; a short secret_len means it was truncated on
           # paste into Render. trailing_ws flags an accidental space/newline.
           "key_len": len(_k), "secret_len": len(_s),
           "key_trailing_ws": _k != _k.strip(), "secret_trailing_ws": _s != _s.strip(),
           # Diagnostics to pinpoint corruption WITHOUT revealing the secret:
           # the real AppSecret has 3 '+' chars and ends in 'k='. If plus<3 the
           # '+' were stripped (URL-decoded to spaces); if tail2!='k=' it was
           # truncated at the end.
           "secret_plus": _s.count("+"), "secret_slash": _s.count("/"),
           "secret_tail2": _s[-2:] if len(_s) >= 2 else _s,
           "secret_has_space": " " in _s,
           # AppKey head/tail (4 chars each) to confirm it matches the issued key
           # (PS5t…Ok4B) — rules out a wrong/mismatched AppKey vs the secret.
           "key_head4": _k[:4], "key_tail4": _k[-4:] if len(_k) >= 4 else _k,
           "key_has_space": " " in _k}
    # Try real (실전) first, then mock (모의/VTS) — EGW00105 'invalid AppSecret'
    # on the wrong domain means the key belongs to the other environment.
    bases = ["https://openapi.koreainvestment.com:9443",
             "https://openapivts.koreainvestment.com:29443"]
    try:
        tok = None
        for base in bases:
            tr = _hx.post(f"{base}/oauth2/tokenP",
                          json={"grant_type": "client_credentials",
                                "appkey": _k, "appsecret": _s}, timeout=15)
            out["token_status"] = tr.status_code
            out["token_msg"] = _safe_msg(tr.text)
            tj = tr.json() if tr.headers.get("content-type", "").startswith("application/json") else {}
            tok = tj.get("access_token")
            if tok:
                out["env"] = "real" if base == bases[0] else "mock"
                break
            if "EGW00105" not in (out.get("token_msg") or ""):
                break
        if tok:
            base = bases[0] if out.get("env") == "real" else bases[1]
            dr = _hx.get(f"{base}/uapi/domestic-futureoption/v1/quotations/display-board-top",
                         headers={"authorization": f"Bearer {tok}",
                                  "appkey": _k, "appsecret": _s,
                                  "tr_id": "FHPIF05030000", "custtype": "P"},
                         params={"FID_COND_MRKT_DIV_CODE": "F", "FID_COND_SCR_DIV_CODE": "20503",
                                 "FID_INPUT_ISCD": "", "FID_INPUT_DATE_1": ""}, timeout=20)
            out["data_status"] = dr.status_code
            out["data_msg"] = _safe_msg(dr.text)
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


@router.get("/kiwoom-deriv-check")
def kiwoom_deriv_check():
    """Replicate the report's derivatives path: build priced rows, compute the KR
    codes exactly as _derivatives_block does, and run it — to see why the
    개별주식선물 sub-table may be missing in the report."""
    out = {}
    try:
        from services import kiwoom_report as kr, kis_derivatives as kd
        rows, _te, _tk, _rate = kr.gather_priced_rows()
        out["rows_debug"] = [{"t": r.get("t"), "mkt": r.get("mkt"),
                              "etf": r.get("etf"), "ko": r.get("ko")} for r in rows]
        kr_codes = [r["t"] for r in rows if r.get("mkt") == "KR" and not r.get("etf")]
        out["kr_codes"] = kr_codes
        sf = kd.stock_futures_all(kr_codes)
        out["stock_futures_all_keys"] = list(sf.keys())
        ko, en, facts = kr._derivatives_block(rows)
        out["block_has_stock_table"] = "개별주식선물" in ko
        out["block_has_index_table"] = "지수 파생" in ko
        out["facts"] = facts[:400]
        out["ko_tail"] = ko[-600:]
    except Exception as e:
        import traceback
        out["error"] = str(e)[:200]
        out["trace"] = traceback.format_exc()[-600:]
    return out


@router.get("/kis-deriv-master")
def kis_deriv_master():
    """Download the KIS derivative master files and surface sample lines + lines
    matching KOSPI200 / 삼성전자(005930) so the futures short codes can be pinned."""
    import io as _io, zipfile as _zip, httpx as _hx
    out = {}
    masters = {
        "fo_idx": "https://new.real.download.dws.co.kr/common/master/fo_idx_code.mst.zip",
        "fo_stk": "https://new.real.download.dws.co.kr/common/master/fo_stk_code.mst.zip",
        "fo_com": "https://new.real.download.dws.co.kr/common/master/fo_com_code.mst.zip",
    }
    for name, url in masters.items():
        try:
            resp = _hx.get(url, timeout=25)
            zf = _zip.ZipFile(_io.BytesIO(resp.content))
            fn = next((n for n in zf.namelist() if n.endswith(".mst")), zf.namelist()[0])
            raw = zf.read(fn).decode("cp949", errors="replace")
            lines = [ln for ln in raw.splitlines() if ln.strip()]
            info = {"file": fn, "total_lines": len(lines),
                    "samples": [ln[:120] for ln in lines[:3]]}
            needles = ["KOSPI200", "코스피200", "K200"] if name == "fo_idx" else ["005930", "삼성전자"]
            hits = [ln[:140] for ln in lines if any(nd in ln for nd in needles)]
            info["match_count"] = len(hits)
            info["matches"] = hits[:6]
            out[name] = info
        except Exception as e:
            out[name + "_err"] = str(e)[:200]
    return out


@router.get("/kis-deriv-live")
def kis_deriv_live(expiry: str = Query(""), fcode: str = Query(""),
                   raw: bool = Query(False)):
    """Exercise the REAL kis_derivatives functions live, or (raw=1) probe the
    board endpoints directly with ?expiry=YYYYMM&fcode=<futures code> so the TR
    params can be pinned without redeploying. Derivatives quotes are not secret."""
    out = {}
    try:
        from services import kis_client, kis_derivatives as kd
        import httpx as _hx
        tok = kis_client.get_token()
        out["token_ok"] = tok is not None
        base = getattr(kis_client, "_active_base", None) or "https://openapi.koreainvestment.com:9443"
        out["active_base"] = base
        if not raw:
            for label, fn in (
                ("active_futures_code", lambda: kd._active_futures_code()),
                ("callput_values", lambda: kd._callput_values()),
                ("derivatives_turnover", lambda: kd.derivatives_turnover()),
                ("stock_futures_005930", lambda: kd.stock_futures("005930")),
                ("stock_futures_all", lambda: kd.stock_futures_all(
                    ["000660", "005930", "017670", "018260", "035420"])),
            ):
                try:
                    out[label] = fn()
                except Exception as e:
                    out[label + "_err"] = str(e)[:200]
            return out

        # raw probes — show rt_cd/msg + output shapes for the two board TRs.
        def _hdr(tr_id):
            k, s = kis_client._creds()
            return {"authorization": f"Bearer {tok}", "appkey": k or "", "appsecret": s or "",
                    "tr_id": tr_id, "custtype": "P", "content-type": "application/json; charset=UTF-8"}

        def _shape(d):
            r = {"rt_cd": d.get("rt_cd"), "msg_cd": d.get("msg_cd"), "msg1": d.get("msg1")}
            for key in ("output", "output1", "output2"):
                v = d.get(key)
                if isinstance(v, list):
                    r[key + "_len"] = len(v)
                    if v and isinstance(v[0], dict):
                        r[key + "_keys"] = list(v[0].keys())
                        r[key + "_row0"] = v[0]
                elif isinstance(v, dict):
                    r[key + "_keys"] = list(v.keys())
                    r[key + "_obj"] = v
            return r

        # callput board (콜/풋) — needs FID_MTRT_CNT = expiry YYYYMM
        try:
            cp = _hx.get(f"{base}/uapi/domestic-futureoption/v1/quotations/display-board-callput",
                         headers=_hdr("FHPIF05030100"),
                         params={"FID_COND_MRKT_DIV_CODE": "O", "FID_COND_SCR_DIV_CODE": "20503",
                                 "FID_MRKT_CLS_CODE": "CO", "FID_MTRT_CNT": expiry or "",
                                 "FID_MRKT_CLS_CODE1": "PO", "FID_COND_MRKT_CLS_CODE": ""}, timeout=20)
            out["callput_raw"] = _shape(cp.json()) if cp.headers.get("content-type","").startswith("application/json") else {"status": cp.status_code, "text": _safe_msg(cp.text)}
        except Exception as e:
            out["callput_raw_err"] = str(e)[:200]
        # futures board (선물) — needs FID_INPUT_ISCD = contract code (e.g. 101W09)
        try:
            ft = _hx.get(f"{base}/uapi/domestic-futureoption/v1/quotations/display-board-top",
                         headers=_hdr("FHPIF05030000"),
                         params={"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": fcode or "101W09",
                                 "FID_COND_MRKT_DIV_CODE1": "", "FID_COND_SCR_DIV_CODE": "20105",
                                 "FID_MTRT_CNT": "", "FID_COND_MRKT_CLS_CODE": ""}, timeout=20)
            out["futures_raw"] = _shape(ft.json()) if ft.headers.get("content-type","").startswith("application/json") else {"status": ft.status_code, "text": _safe_msg(ft.text)}
        except Exception as e:
            out["futures_raw_err"] = str(e)[:200]
        # Also probe the single-instrument 시세 (FHMIF10000000) for the front-month
        # futures code — carries 누적거래대금 (acml_tr_pbmn) directly.
        try:
            ip = _hx.get(f"{base}/uapi/domestic-futureoption/v1/quotations/inquire-price",
                         headers=_hdr("FHMIF10000000"),
                         params={"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": fcode or "101W09"},
                         timeout=20)
            out["inquire_price_raw"] = _shape(ip.json()) if ip.headers.get("content-type","").startswith("application/json") else {"status": ip.status_code, "text": _safe_msg(ip.text)}
        except Exception as e:
            out["inquire_price_raw_err"] = str(e)[:200]
        # daily-chart (FHKIF03020100) — what _instrument_turnover uses (acml_tr_pbmn)
        try:
            from datetime import datetime as _dt2, timedelta as _td2
            d2 = _dt2.utcnow().strftime("%Y%m%d"); d1 = (_dt2.utcnow() - _td2(days=10)).strftime("%Y%m%d")
            dc = _hx.get(f"{base}/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice",
                         headers=_hdr("FHKIF03020100"),
                         params={"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": fcode or "1A01609",
                                 "FID_INPUT_DATE_1": d1, "FID_INPUT_DATE_2": d2, "FID_PERIOD_DIV_CODE": "D"},
                         timeout=20)
            out["daily_chart_raw"] = _shape(dc.json()) if dc.headers.get("content-type","").startswith("application/json") else {"status": dc.status_code, "text": _safe_msg(dc.text)}
        except Exception as e:
            out["daily_chart_raw_err"] = str(e)[:200]
    except Exception as e:
        out["error"] = str(e)[:200]
    return out


@router.get("/youtube/latest")
def youtube_latest(db: Session = Depends(get_db)):
    """Latest GROUNDED YouTube report (delivery_channel='gpu_youtube') for the web
    UI tab — structured rows/recommendations/summaries/sources + the 4 file URLs +
    the 'generated_at_kst' so the UI can show it's a morning snapshot. Returns
    {available: false} if the colleague's pipeline hasn't written a row yet."""
    from services import youtube_grounded
    payload = youtube_grounded.latest_payload(db)
    if not payload:
        return {"available": False}
    return {"available": True, **payload}


@router.get("/llm-check")
def llm_check():
    """Diagnostic: do a tiny prefer_paid test call and report which provider
    actually answered (confirms the OpenAI key + budget pipeline)."""
    from services.llm_client import chat_completion_sync, get_last_provider, get_budget_status
    reply = chat_completion_sync(
        "You are a connectivity test.",
        [{"role": "user", "content": "Reply with the single word OK."}],
        max_tokens=5, temperature=0, prefer_paid=True)
    return {"provider_used": get_last_provider(), "budget": get_budget_status(),
            "reply": (reply or "")[:80]}


@router.get("/email-config")
def email_config():
    """Diagnostic health-check for the report email sender — BOOLEANS ONLY, and
    gated behind EXPOSE_DIAGNOSTICS=1 (off in production → 404). Returns no
    addresses, host, recipient values, or the password."""
    if os.getenv("EXPOSE_DIAGNOSTICS") != "1":
        raise HTTPException(404, "Not found")
    from services import report_email
    return {
        "smtp_configured": report_email.is_configured(),
        "smtp_host_set": bool(os.getenv("SMTP_HOST")),  # host defaults to gmail if unset
        "sender_set": bool(report_email.sender_address()),  # SMTP_USER or SMTP_EMAIL
        "password_set": bool(os.getenv("SMTP_PASSWORD")),
        "from_name_set": bool(os.getenv("SMTP_FROM_NAME")),
        "use_tls": os.getenv("SMTP_USE_TLS", "1"),
        "recipient_set": bool(os.getenv("KIWOOM_REPORT_EMAIL")),
        "note": "Booleans only. Sender = SMTP_USER or SMTP_EMAIL; host defaults to "
                "smtp.gmail.com. If smtp_configured is false, set SMTP_EMAIL + SMTP_PASSWORD.",
    }


@router.post("/test-email", dependencies=[Depends(rate_limit_compose)])
def test_email_send():
    """Synchronous SMTP self-test: send a tiny .docx to the DEFAULT recipient
    and return the ACTUAL SMTP result (ok + reason) so delivery failures are
    visible inline (e.g. Gmail rejecting a non-App-Password). No user input —
    recipient is the server-side default only."""
    from services import report_email
    from services.report_docx import markdown_to_docx
    if not report_email.is_configured():
        return {"ok": False,
                "reason": "SMTP not configured — need SMTP_EMAIL (or SMTP_USER) + SMTP_PASSWORD",
                "sender_set": bool(report_email.sender_address()),
                "password_set": bool(os.getenv("SMTP_PASSWORD"))}
    docx = markdown_to_docx(
        "# Email Test\n\nIf you received this, report email delivery works.\n\n"
        "| Check | Result |\n|---|---|\n| SMTP | OK |",
        "TripleH Email Test", "diagnostic")
    res = report_email.send_email_with_docx(
        report_email.DEFAULT_RECIPIENT,
        "[TripleH] Email delivery test",
        "This is a delivery test from the VIP orchestrator. "
        "If you see this with the attached .docx, report email works.",
        "TripleH_Email_Test.docx", docx)
    return res


@router.post("/test-news", dependencies=[Depends(rate_limit_compose)])
def test_news_provider():
    """Diagnostic: run one live web search and report which provider answered
    (or that none is configured). Returns provider name + result count + which
    search keys are present — no query content, no secrets."""
    from services.web_search import search_web, gemini_search_models
    res = search_web("KOSPI stock market news today", num_results=3)
    try:
        gmodels = gemini_search_models()[:12]
    except Exception:
        gmodels = []
    return {
        "ok": bool(res.get("ok")),
        "provider": res.get("provider"),
        "result_count": len(res.get("results", [])),
        "error": res.get("error"),
        "gemini_models_available": gmodels,
        "keys_present": {
            "SERPER_API_KEY": bool(os.getenv("SERPER_API_KEY")),
            "TAVILY_API_KEY": bool(os.getenv("TAVILY_API_KEY")),
            "GOOGLE_CSE_KEY": bool(os.getenv("GOOGLE_CSE_KEY")),
            "GEMINI_or_GOOGLE_API_KEY": bool(os.getenv("GEMINI_API_KEY")
                                             or os.getenv("GOOGLE_API_KEY")
                                             or os.getenv("GOOGLE_GENERATIVE_AI_API_KEY")),
        },
    }


@router.post("/compose/daily", dependencies=[Depends(rate_limit_compose)])
def compose_daily(body: ComposeBody, db: Session = Depends(get_db)):
    """Compose a daily executive summary from the last 24h of task runs."""
    return report_service.compose_report(
        db, report_type="daily_summary",
        hours_back=body.hours_back, delivery_channel=body.delivery_channel, trace_id=body.trace_id,
    )


@router.post("/compose/weekly", dependencies=[Depends(rate_limit_compose)])
def compose_weekly(body: ComposeBody, db: Session = Depends(get_db)):
    """Compose a weekly summary from the last 168h of task runs."""
    return report_service.compose_report(
        db, report_type="weekly_summary",
        hours_back=max(body.hours_back, 168), delivery_channel=body.delivery_channel, trace_id=body.trace_id,
    )


@router.post("/compose/alert", dependencies=[Depends(rate_limit_compose)])
def compose_alert(body: ComposeBody, db: Session = Depends(get_db)):
    """Compose an urgent alert summary from recent task runs."""
    return report_service.compose_report(
        db, report_type="urgent_alert_summary",
        hours_back=body.hours_back, delivery_channel=body.delivery_channel, trace_id=body.trace_id,
    )


class CrossAgentReportBody(BaseModel):
    agent_types: list[str] = Field(..., description="List of agent types to include (e.g., ['asset', 'stock', 'realty'])")
    report_type: str = Field(default="cross_agent_summary")
    delivery_channel: str = Field(default="web")
    trace_id: str = Field(default="system")

    model_config = {"json_schema_extra": {"examples": [
        {
            "agent_types": ["asset", "stock", "realty"],
            "report_type": "cross_agent_summary",
            "trace_id": "tr-report-001",
        }
    ]}}


@router.post("/compose/cross-agent", dependencies=[Depends(rate_limit_compose)])
def compose_cross_agent(body: CrossAgentReportBody, db: Session = Depends(get_db)):
    """
    Compose a combined report by fetching real-time data from multiple agents via A2A.
    Each agent is queried through the A2A data request flow.
    """
    return report_service.compose_cross_agent_report(
        db,
        agent_types=body.agent_types,
        report_type=body.report_type,
        trace_id=body.trace_id,
        delivery_channel=body.delivery_channel,
    )


@router.get("/{report_id}")
def get_report(report_id: UUID, db: Session = Depends(get_db)):
    """Get a report by ID with full JSON content."""
    report = report_service.get_report(db, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return report


@router.delete("/{report_id}")
def delete_report(report_id: UUID, db: Session = Depends(get_db)):
    """Delete a report."""
    report = db.query(OrchReport).filter(OrchReport.id == report_id).first()
    if not report:
        raise HTTPException(404, "Report not found")
    db.delete(report)
    db.commit()
    return {"deleted": True, "id": str(report_id)}


@router.get("/{report_id}/markdown", response_class=PlainTextResponse)
def get_report_markdown(report_id: UUID, db: Session = Depends(get_db)):
    """Get a report in Markdown format."""
    report = report_service.get_report(db, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    md = report.get("content", {}).get("markdown", "# No markdown available")
    return md


# Internal/non-user-facing report rows. These are written frequently (hourly
# snapshots) or are bookkeeping (recommendation history) — if they're returned in
# the default list they bury the once-daily Kiwoom/Newspaper/YouTube/Master
# reports out of the window, so the dashboard tabs show "(0)". Hidden unless a
# caller asks for that exact report_type.
_INTERNAL_REPORT_TYPES = (
    "kiwoom_snapshot", "newspaper_snapshot", "youtube_snapshot",
    "recommendation_daily",
)


@router.get("/")
def list_reports(
    report_type: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
):
    """List reports (newest first). Excludes internal hourly snapshots +
    recommendation history unless a specific report_type is requested, so the
    daily reports always stay visible in the dashboard tabs."""
    q = db.query(OrchReport)
    if report_type:
        q = q.filter(OrchReport.report_type == report_type)
    else:
        q = q.filter(OrchReport.report_type.notin_(_INTERNAL_REPORT_TYPES))
    reports = q.order_by(OrchReport.created_at.desc()).limit(limit).all()
    return [
        {
            "id": str(r.id),
            "report_type": r.report_type,
            # Expose the period (daily/weekly/monthly) so the dashboard can sort a
            # report into the right tab; it lives inside content_json, defaults daily.
            "period": (r.content_json or {}).get("period", "daily"),
            "delivery_channel": r.delivery_channel,
            "source_run_count": len(r.source_run_ids_json) if r.source_run_ids_json else 0,
            "executive_summary": (r.content_json or {}).get("executive_summary", ""),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reports
    ]
