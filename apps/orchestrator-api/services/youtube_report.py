"""
youtube_report — daily market analysis built from REAL content of 4 finance
YouTube channels. For each channel the agent pulls the video's actual transcript
(youtube-transcript-api, no key) plus the real title (oEmbed) and recent related
coverage (Serper search), then the LLM writes a per-channel deep analysis of the
watchlist companies + BUY/HOLD/SELL suggestions.

Channels (user-specified):
  1. Bloomberg TV          — https://www.youtube.com/watch?v=iEpJwprxDdk
  2. WSJ News              — (resolved via search)
  3. 한국경제TV (hkwow)     — https://www.youtube.com/watch?v=bRBtOPYU414
  4. 매일경제 (매경)        — https://www.youtube.com/watch?v=s9xL1DpBsfQ

Reuses the Kiwoom price layer for the watchlist table. Saved as
report_type='youtube_report' (period='daily'); also Telegram + Word email.
NEVER mocks — if a transcript is unavailable (e.g. a live stream), it uses the
real title + real search snippets and says so.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from services.logger import log
from services import kiwoom_report as _kr
from services import catalyst_news as _cat

# (name, video_id|None, search query to find recent real coverage)
YT_CHANNELS: list[dict] = [
    {"name": "Bloomberg TV", "video_id": "iEpJwprxDdk",
     "query": "Bloomberg TV semiconductor Nvidia Samsung SK Hynix AMD stock market today"},
    {"name": "WSJ News", "video_id": None,
     "query": "Wall Street Journal markets video semiconductor Nvidia Samsung Micron today"},
    {"name": "한국경제TV (한경 와우)", "video_id": "bRBtOPYU414",
     "query": "한국경제TV 반도체 삼성전자 SK하이닉스 네이버 증시 전망 today"},
    {"name": "매일경제 (매경)", "video_id": "s9xL1DpBsfQ",
     "query": "매일경제 MBN 증시 반도체 삼성전자 SK하이닉스 코스피 전망 today"},
]


def _video_id_from_url(url: str) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|/live/|/embed/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else None


def _oembed_title(video_id: str) -> str:
    """Real video title + channel via YouTube oEmbed (no API key)."""
    try:
        with httpx.Client(timeout=10) as c:
            r = c.get("https://www.youtube.com/oembed",
                      params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"})
        if r.status_code == 200:
            j = r.json()
            return f"{j.get('title', '')} — {j.get('author_name', '')}".strip(" —")
    except Exception:
        pass
    return ""


def _transcript(video_id: str, max_chars: int = 6000) -> str:
    """Fetch the real transcript text (Korean or English). Empty string if the
    video has no transcript (e.g. a live stream) or YouTube blocks the fetch."""
    if not video_id:
        return ""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        segs = None
        for langs in (["ko"], ["en"], ["ko", "en", "en-US"]):
            try:
                segs = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
                if segs:
                    break
            except Exception:
                continue
        if not segs:
            try:
                segs = YouTubeTranscriptApi.get_transcript(video_id)
            except Exception:
                segs = None
        if not segs:
            return ""
        text = " ".join(s.get("text", "") for s in segs if s.get("text"))
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception as e:
        log.warning(f"youtube: transcript {video_id} failed: {str(e)[:100]}")
        return ""


def _channel_id(video_id: str) -> str | None:
    """Resolve the channelId from a video's watch page (public HTML, not the
    IP-blocked transcript API)."""
    if not video_id:
        return None
    try:
        with httpx.Client(timeout=10, headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = c.get(f"https://www.youtube.com/watch?v={video_id}")
        m = re.search(r'"channelId":"(UC[\w-]{20,})"', r.text)
        return m.group(1) if m else None
    except Exception:
        return None


def _latest_uploads(channel_id: str, n: int = 4) -> list[dict]:
    """Latest uploads for a channel via the free YouTube RSS feed (real titles +
    descriptions, daily-fresh, no API key, not IP-blocked)."""
    if not channel_id:
        return []
    try:
        import defusedxml.ElementTree as ET
        with httpx.Client(timeout=10) as c:
            r = c.get("https://www.youtube.com/feeds/videos.xml",
                      params={"channel_id": channel_id})
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
        ns = {"a": "http://www.w3.org/2005/Atom",
              "m": "http://search.yahoo.com/mrss/",
              "yt": "http://www.youtube.com/xml/schemas/2015"}
        out = []
        for e in root.findall("a:entry", ns)[:n]:
            vid = e.find("yt:videoId", ns)
            title = e.find("a:title", ns)
            pub = e.find("a:published", ns)
            desc = e.find("m:group/m:description", ns)
            out.append({
                "video_id": vid.text if vid is not None else "",
                "title": title.text if title is not None else "",
                "published": pub.text if pub is not None else "",
                "description": ((desc.text or "")[:700] if desc is not None else ""),
            })
        return out
    except Exception as e:
        log.warning(f"youtube: RSS {channel_id} failed: {str(e)[:100]}")
        return []


def _search_channel(query: str, n: int = 8) -> list[dict]:
    try:
        from services.web_search import search_web
        res = search_web(query, num_results=n)
        if res.get("ok"):
            return [{"title": (h.get("title") or "").strip(),
                     "url": h.get("url", ""),
                     "snippet": (h.get("snippet") or "").strip()}
                    for h in res.get("results", [])]
    except Exception as e:
        log.warning(f"youtube: search failed: {str(e)[:100]}")
    return []


def _gather_channels() -> list[dict]:
    """Collect REAL data per channel: the channel's LATEST UPLOADS (titles +
    descriptions via free RSS) + a transcript of the newest upload when one is
    available + recent related search coverage. The given URLs are usually live
    streams (no transcript), so we analyse the latest VODs instead."""
    out = []
    for ch in YT_CHANNELS:
        vid = ch.get("video_id")
        hits = _search_channel(ch["query"])
        if not vid:  # WSJ — resolve a recent video from search results.
            for h in hits:
                vid = _video_id_from_url(h.get("url", ""))
                if vid:
                    break
        live_title = _oembed_title(vid) if vid else ""
        # Latest uploaded videos (VODs) for this channel via RSS.
        uploads = _latest_uploads(_channel_id(vid), n=6) if vid else []
        # Try a transcript on the newest non-live upload (best real content).
        transcript = ""
        for up in uploads:
            transcript = _transcript(up.get("video_id", ""))
            if transcript:
                up["transcribed"] = True
                break
        out.append({
            "name": ch["name"],
            "video_id": vid,
            "video_url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
            "title": live_title,
            "uploads": uploads,
            "transcript": transcript,
            "has_transcript": bool(transcript),
            "hits": hits,
        })
    return out


def _channel_block(channels: list[dict]) -> str:
    parts = []
    for ch in channels:
        parts.append(f"### {ch['name']}")
        if ch.get("title"):
            parts.append(f"Live/feed: {ch['title']} ({ch['video_url']})")
        # Latest uploaded videos (titles + real descriptions) — daily-fresh.
        if ch.get("uploads"):
            parts.append("LATEST UPLOADED VIDEOS (real titles + descriptions):")
            for up in ch["uploads"][:6]:
                t = (up.get("title") or "")[:150]
                dsc = (up.get("description") or "").replace("\n", " ")[:350]
                line = f"- {t}"
                if up.get("published"):
                    line += f"  [{up['published'][:10]}]"
                if dsc:
                    line += f" — {dsc}"
                parts.append(line)
        if ch.get("transcript"):
            parts.append(f"TRANSCRIPT of newest upload (real spoken content, excerpt):\n{ch['transcript'][:4500]}")
        else:
            parts.append("TRANSCRIPT: none (live stream / captions unavailable) — "
                         "analyse from the real upload titles + descriptions + coverage.")
        if ch.get("hits"):
            parts.append("RECENT RELATED COVERAGE (web):")
            for h in ch["hits"][:8]:
                t = h["title"][:130]
                s = h["snippet"][:200]
                parts.append(f"- {t} — {s}" if s else f"- {t}")
        parts.append("")
    return "\n".join(parts)


def build_youtube_report(db, trace_id: str) -> dict:
    """Build the daily YouTube analysis report — real channel content + the same
    watchlist table as Kiwoom + per-channel deep analysis + suggestions, EN/KO."""
    rows, table_en, table_ko, rate = _kr.gather_priced_rows()
    ok_rows = [r for r in rows if r.get("ok")]
    channels = _gather_channels()
    catalysts = _cat.gather_catalysts()
    n_transcripts = sum(1 for c in channels if c["has_transcript"])
    from services.kst import kst_date as _kst_date
    kst_date = _kst_date()

    movers = sorted([r for r in ok_rows if r.get("change_pct") is not None],
                    key=lambda r: r["change_pct"])
    sum_en = (f"YouTube analysis ({len(channels)} channels, {n_transcripts} transcripts): "
              + (f"weakest {movers[0]['en']} {_kr._fmt_chg(movers[0]['change_pct'])}, "
                 f"strongest {movers[-1]['en']} {_kr._fmt_chg(movers[-1]['change_pct'])}."
                 if movers else "data limited."))
    sum_ko = (f"유튜브 분석 ({len(channels)}개 채널, 자막 {n_transcripts}건): "
              + (f"최약 {movers[0]['ko']} {_kr._fmt_chg(movers[0]['change_pct'])}, "
                 f"최강 {movers[-1]['ko']} {_kr._fmt_chg(movers[-1]['change_pct'])}."
                 if movers else "데이터 제한."))

    chan_names = ", ".join(c["name"] for c in channels)
    detail_en = detail_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        sysmsg = (
            "You are TripleH's market-video analyst writing the DAILY YouTube report "
            "after the US close (~6:30 AM KST). You watch 4 finance channels "
            f"({chan_names}) and summarise what they say about the watchlist, then "
            "give BUY/HOLD/SELL suggestions. Use ONLY the provided transcripts / "
            "titles / coverage + the price data — NEVER invent quotes. If a channel "
            "had no transcript (live stream), say so and analyse from its real title "
            "+ recent coverage. ALL prices are Korean Won (KRW). Produce EXACTLY (do "
            "NOT include a price/market-data table — that lives only in the Kiwoom "
            "report):\n"
            "## 1. General Overview\n## 2. Channel-by-Channel Analysis\n"
            "## 3. Company-Specific Analysis\n## 4. Catalysts & Schedule (일정매매)\n"
            "## 5. Recommendations\n\n"
            "Rules:\n"
            "- Section 2 (Channel-by-Channel) — the CORE section. For EACH channel a "
            "'### <Channel>' sub-heading with a DEEP-DIVE of 350-450 words (about "
            "HALF a page, 4-5 full paragraphs): (1) what the channel/host covered in "
            "its latest uploads (use the real upload TITLES + DESCRIPTIONS), (2) its "
            "market & macro view, (3) its sector/semiconductor view, (4) its take on "
            "our watchlist companies — name them with the real figures — and (5) any "
            "forward calls or sentiment. Quote concrete points from the transcript / "
            "upload descriptions / coverage. If no transcript, note it briefly and "
            "analyse fully from the real upload titles, descriptions and coverage — "
            "do NOT make the section short just because there is no transcript.\n"
            "- Section 3 (Company-Specific): a dedicated paragraph per name (SK Hynix, "
            "Samsung, AMD, Micron, Broadcom, SanDisk, SOXX, SK Telecom, Samsung SDS, "
            "Naver, KODEX 200) — combine what the channels said with the real change%.\n"
            f"- Section 4 (Catalysts & Schedule / 일정매매): {_cat.CATALYST_SECTION_RULE}\n"
            "- Section 5 (Recommendations): FIRST a table | Stock | Action | Reason | "
            "(BUY/HOLD/SELL); THEN a '### Rationale' subsection. TIE each call to a "
            "CATALYST and TIMING where possible (event-driven / 일정매매 — 'BUY before "
            "<event/date>, sell into the attention') from the videos + price action.\n"
            "The WHOLE report must be at LEAST 3 pages — aim for 2600-3200 words; "
            "Section 2 (Channel-by-Channel) ~1700 words (≈half a page × 4 channels) and "
            "Section 4 (Catalysts) a full, detailed section. Never truncate a section.\n"
            "Output ONLY the finished English Markdown report — no preamble."
        )
        user = (f"Date (KST): {kst_date} · USD/KRW: {rate:,.0f}\n\n"
                f"PRICE CONTEXT (for your analysis only — do NOT print a table):\n{_kr._facts(rows)}\n\n"
                f"CHANNELS (real data):\n{_channel_block(channels)}\n\n"
                f"CATALYST / EVENT DATA (for Section 4):\n{_cat.catalyst_block(catalysts)}")
        out = chat_completion_sync(
            system_prompt=sysmsg, messages=[{"role": "user", "content": user[:26000]}],
            max_tokens=12000, temperature=0.5, model="groq-llama-3.3-70b") or ""
        bad = (not out.strip()) or out.lstrip().startswith(("[LLM unavailable]", "[server error]"))
        if not bad:
            detail_en = out.strip()
            try:
                ko_sys = (
                    "You are a professional Korean financial translator. Translate the "
                    "ENTIRE English YouTube report into natural, professional Korean "
                    "(존댓말). Translate EVERYTHING — never summarise or stub. Preserve "
                    "ALL Markdown, headings, sub-headings and tables; keep every number, "
                    "%, 원, ticker and channel name IDENTICAL. Replace the Section 2 "
                    f"table with this EXACT Korean table:\n{table_ko}\n"
                    "Output ONLY the Korean Markdown report.")
                ko_out = chat_completion_sync(
                    system_prompt=ko_sys,
                    messages=[{"role": "user", "content": detail_en[:24000]}],
                    max_tokens=11000, temperature=0.3, model="groq-llama-3.3-70b") or ""
                ko_bad = ((not ko_out.strip())
                          or ko_out.lstrip().startswith(("[LLM unavailable]", "[server error]"))
                          or len(ko_out.strip()) < 400)
                if not ko_bad:
                    detail_ko = ko_out.strip()
            except Exception as e:
                log.warning(f"youtube KO translation failed: {e}")
    except Exception as e:
        log.warning(f"youtube LLM compose failed: {e}")

    if not detail_en:
        cl = []
        for c in channels:
            cl.append(f"### {c['name']}")
            cl.append(f"- {c.get('title') or '(title unavailable)'}")
            cl += [f"- {h['title']}" for h in c.get("hits", [])[:4]]
        detail_en = (f"# YouTube Market Analysis\n*{kst_date} (after US close)*\n\n"
                     f"## 1. General Overview\n{sum_en}\n\n"
                     f"## 2. Channel-by-Channel Analysis\n" + "\n".join(cl) + "\n\n"
                     f"## 3. Company-Specific Analysis\nSee channel notes above.\n\n"
                     f"## 4. Catalysts & Schedule (일정매매)\n"
                     + _cat.catalyst_block(catalysts) + "\n\n"
                     f"## 5. Recommendations\n| Stock | Action | Reason |\n|---|---|---|\n"
                     f"| — | HOLD | LLM unavailable — manual review |")
    if not detail_ko:
        detail_ko = detail_en

    return {
        "agent_type": "youtube", "name": "YouTube Market Analysis", "emoji": "📺",
        "status": "ok" if ok_rows else "partial",
        "summary_en": sum_en, "summary_ko": sum_ko,
        "detail_en": detail_en, "detail_ko": detail_ko,
        "table_en": table_en, "table_ko": table_ko,
        "rows": rows,
        "channels": [{"name": c["name"], "video_url": c["video_url"],
                      "title": c["title"], "has_transcript": c["has_transcript"]} for c in channels],
        "source": "TripleH YouTube Analysis (Bloomberg TV / WSJ / 한국경제TV / 매일경제 + OHLCV)",
    }
