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
    # Korean channels FIRST (한국 채널 우선), then US/overseas.
    {"name": "한국경제TV (한경 와우)", "video_id": "bRBtOPYU414",
     "query": "한국경제TV 반도체 삼성전자 SK하이닉스 네이버 증시 전망 today"},
    {"name": "매일경제 (매경)", "video_id": "s9xL1DpBsfQ",
     "query": "매일경제 MBN 증시 반도체 삼성전자 SK하이닉스 코스피 전망 today"},
    {"name": "Bloomberg TV", "video_id": "iEpJwprxDdk",
     "query": "Bloomberg TV semiconductor Nvidia Samsung SK Hynix AMD stock market today"},
    {"name": "WSJ News", "video_id": None,
     "query": "Wall Street Journal markets video semiconductor Nvidia Samsung Micron today"},
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


def _within_24h(published: str) -> bool:
    """True if an Atom 'published' timestamp is within the last 24 hours."""
    if not published:
        return False
    try:
        from datetime import datetime, timedelta, timezone
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= datetime.now(timezone.utc) - timedelta(hours=24)
    except Exception:
        return False


def _split_enko(out: str) -> tuple[str, str]:
    out = (out or "").strip()
    if (not out) or out.lstrip().startswith(("[LLM unavailable]", "[server error]")):
        return "", ""
    if "===KO===" in out:
        a, b = out.split("===KO===", 1)
        return a.replace("===EN===", "").strip(), b.strip()
    return out.replace("===EN===", "").strip(), ""


def _video_links_md(clips: list[dict]) -> str:
    """Clickable youtube.com video links (with publish time) for verification."""
    lines = []
    for c in clips:
        u = c.get("url") or ""
        if "youtube.com" not in u and "youtu.be" not in u:
            continue
        t = (c.get("title") or u).replace("[", "(").replace("]", ")")[:120]
        pub = c.get("published")
        when = f" — {pub[:16].replace('T', ' ')}" if pub else ""
        tag = " 🎙️자막" if c.get("has_transcript") else ""
        lines.append(f"- [{t}]({u}){when}{tag}")
    return "\n".join(lines) if lines else "- (no videos in the last 24h)"


def _gather_channels(per_channel: int = 3, max_seconds: int = 1500) -> list[dict]:
    """Per channel: the last-24h UPLOADED clips (RSS, timestamped) with a REAL
    Whisper transcript of each (yt-dlp + Groq) when obtainable; falls back to the
    video description. Live streams / long / IP-blocked clips → description only."""
    from services import audio_transcribe
    out = []
    for ch in YT_CHANNELS:
        vid = ch.get("video_id")
        hits = _search_channel(ch["query"])
        if not vid:  # resolve a channel video from search if no fixed id
            for h in hits:
                vid = _video_id_from_url(h.get("url", ""))
                if vid:
                    break
        chan_id = _channel_id(vid) if vid else None
        uploads = _latest_uploads(chan_id, n=15) if chan_id else []
        recent = [u for u in uploads if _within_24h(u.get("published"))][:per_channel]
        clips = []
        n_trans = 0
        for u in recent:
            vidid = u.get("video_id", "")
            tx = audio_transcribe.transcribe_youtube(vidid, max_seconds=max_seconds)
            if tx:
                n_trans += 1
            clips.append({
                "video_id": vidid,
                "title": u.get("title", ""),
                "url": f"https://www.youtube.com/watch?v={vidid}",
                "published": u.get("published", ""),
                "description": u.get("description", ""),
                "transcript": tx, "has_transcript": bool(tx),
            })
        out.append({
            "name": ch["name"], "video_id": vid,
            "video_url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
            "clips": clips, "n_transcripts": n_trans, "uploads_24h": len(recent),
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


_WATCHLIST = ("SK Hynix, Samsung Electronics, Naver, SK Telecom, Samsung SDS, "
              "AMD, Micron, Broadcom, SanDisk, SOXX, KODEX 200")


def _merge_hourly_videos(db, channels: list[dict], cap_per_channel: int = 8) -> list[dict]:
    """Fold the day's hourly YouTube snapshots into the fresh gather (dedupe by
    video id, cap per channel) so the morning report covers the whole day's
    uploads, not just the latest fetch."""
    try:
        from services import hourly_capture
        acc = hourly_capture.accumulated(db, "youtube", hours=26)
    except Exception as e:
        log.warning(f"youtube hourly merge skipped: {str(e)[:80]}")
        return channels
    by_ch: dict[str, list[dict]] = {}
    for it in acc:
        by_ch.setdefault(it.get("channel", ""), []).append(it)
    for ch in channels:
        clips = ch.get("clips", [])
        vids = {c.get("video_id") for c in clips}
        for it in by_ch.get(ch["name"], []):
            if len(clips) >= cap_per_channel:
                break
            if it.get("video_id") and it["video_id"] not in vids:
                vids.add(it["video_id"])
                clips.append({"video_id": it["video_id"], "title": it.get("title", ""),
                              "url": it.get("url", ""), "published": it.get("published"),
                              "description": it.get("description", ""),
                              "transcript": "", "has_transcript": False})
        ch["clips"] = clips[:cap_per_channel]
        ch["uploads_24h"] = len(ch["clips"])
    return channels


def build_youtube_report(db, trace_id: str) -> dict:
    """Daily YouTube report — for each channel, the last-24h uploaded clips with
    REAL Whisper transcripts (yt-dlp + Groq) where obtainable; a dedicated deep
    per-channel analysis; clickable video links; bilingual EN/KO."""
    rows, table_en, table_ko, rate = _kr.gather_priced_rows()
    ok_rows = [r for r in rows if r.get("ok")]
    channels = _gather_channels()
    # Merge the day's hourly video snapshots (the '24 parts').
    channels = _merge_hourly_videos(db, channels)
    catalysts = _cat.gather_catalysts()
    n_transcripts = sum(c.get("n_transcripts", 0) for c in channels)
    from services.kst import kst_date as _kst_date
    kst_date = _kst_date()

    transcript_stats = {c["name"]: {"clips_24h": c.get("uploads_24h", 0),
                                    "transcribed": c.get("n_transcripts", 0)}
                        for c in channels}

    movers = sorted([r for r in ok_rows if r.get("change_pct") is not None],
                    key=lambda r: r["change_pct"])
    sum_en = (f"YouTube analysis ({len(channels)} channels, {n_transcripts} real transcripts): "
              + (f"weakest {movers[0]['en']} {_kr._fmt_chg(movers[0]['change_pct'])}, "
                 f"strongest {movers[-1]['en']} {_kr._fmt_chg(movers[-1]['change_pct'])}."
                 if movers else "data limited."))
    sum_ko = (f"유튜브 분석 ({len(channels)}개 채널, 실제 자막 {n_transcripts}건): "
              + (f"최약 {movers[0]['ko']} {_kr._fmt_chg(movers[0]['change_pct'])}, "
                 f"최강 {movers[-1]['ko']} {_kr._fmt_chg(movers[-1]['change_pct'])}."
                 if movers else "데이터 제한."))

    detail_en = detail_ko = ""
    try:
        from services.llm_client import chat_completion_sync
        import time as _t

        sec_en, sec_ko = {}, {}
        for ch in channels:
            name = ch["name"]
            clips = ch.get("clips", [])
            if not clips:
                sec_en[name] = f"### {name}\nNo uploads in the last 24 hours."
                sec_ko[name] = f"### {name}\n지난 24시간 내 업로드가 없습니다."
                continue
            has_tx = any(c.get("has_transcript") for c in clips)
            corpus = "\n\n".join(
                (f"[TRANSCRIPT] {c.get('title','')}\n{(c.get('transcript') or '')[:5000]}"
                 if c.get("has_transcript")
                 else f"[DESCRIPTION ONLY] {c.get('title','')}\n{(c.get('description') or '')[:700]}")
                for c in clips)
            cov = "\n".join(f"- {h.get('title','')[:120]}" for h in ch.get("hits", [])[:5])
            if has_tx:
                length = "700-1000 words (a deep analysis of what was actually said)"
                note = ("[TRANSCRIPT] items are the REAL spoken words — analyse them in "
                        "depth and quote concrete points.")
            else:
                length = "180-260 words (only titles/descriptions are available)"
                note = ("No transcript was obtainable (live/blocked) — analyse honestly "
                        "from the real titles + descriptions; do NOT invent.")
            sysd = (
                f"You are TripleH's market-video analyst. Below are {name}'s videos "
                "UPLOADED in the LAST 24 HOURS. Write " + length + " covering what the "
                "channel said about: the market, market-moving politics/policy, the "
                f"semiconductor sector, and the watchlist ({_WATCHLIST}). Explain the "
                f"price impact in flowing prose. {note} Use ONLY the provided content; "
                "NEVER invent quotes or numbers. Do NOT put URLs in your text (a "
                f"verified video-link list is appended separately). Begin with '### {name}'. "
                "Output EXACTLY:\n===EN===\n<english>\n===KO===\n<korean 존댓말, same depth>")
            try:
                out = chat_completion_sync(
                    system_prompt=sysd, messages=[{"role": "user", "content": (corpus + "\n\nRECENT COVERAGE:\n" + cov)[:20000]}],
                    max_tokens=6000, temperature=0.45, model="groq-llama-3.3-70b", prefer_paid=True) or ""
                en, ko = _split_enko(out)
                sec_en[name] = en or (f"### {name}\n" + "\n".join(f"- {c.get('title','')}" for c in clips))
                sec_ko[name] = ko or sec_en[name]
            except Exception as e:
                log.warning(f"youtube channel {name} failed: {str(e)[:100]}")
                sec_en[name] = f"### {name}\n" + "\n".join(f"- {c.get('title','')}" for c in clips)
                sec_ko[name] = sec_en[name]
            links = _video_links_md(clips)
            sec_en[name] = sec_en[name].rstrip() + f"\n\n**🔗 Source videos (click to verify):**\n{links}"
            sec_ko[name] = sec_ko[name].rstrip() + f"\n\n**🔗 출처 영상 (클릭하여 확인):**\n{links}"
            _t.sleep(0.4)

        chan_en = "\n\n".join(sec_en[c["name"]] for c in channels)
        chan_ko = "\n\n".join(sec_ko[c["name"]] for c in channels)

        digest = "\n\n".join(f"[{c['name']}] " + sec_en.get(c["name"], "")[:600] for c in channels)
        ssys = (
            "You are TripleH's chief market analyst. Using the per-channel YouTube "
            "summaries + price + catalyst data, write these sections (do NOT write a "
            "'Channel-by-Channel' section — added separately; do NOT print a price "
            "table). ALL prices KRW. Sections:\n"
            "## 1. General Overview\n## 3. Company-Specific Analysis\n"
            "## 4. Catalysts & Schedule (일정매매)\n## 5. Recommendations\n\n"
            "- Section 1: 2-3 paragraph read of what the channels covered in the last 24h.\n"
            f"- Section 3: a 4-6 sentence paragraph per name ({_WATCHLIST}) tying the "
            "video commentary to the real change% + technicals.\n"
            f"- Section 4 (일정매매): {_cat.CATALYST_SECTION_RULE}\n"
            "- Section 5: table | Stock | Action | Reason | (BUY/HOLD/SELL) THEN '### "
            "Rationale' tying each to a catalyst + timing.\n"
            "Use ONLY provided data. Output EXACTLY:\n===EN===\n<english>\n===KO===\n<korean 존댓말>")
        suser = (f"TODAY (KST): {kst_date} · USD/KRW: {rate:,.0f}\n\n"
                 f"PRICE CONTEXT (no table):\n{_kr._facts(rows)}\n\n"
                 f"PER-CHANNEL SUMMARIES:\n{digest}\n\nCATALYST DATA:\n{_cat.catalyst_block(catalysts)}")
        syn_en = syn_ko = ""
        try:
            out = chat_completion_sync(
                system_prompt=ssys, messages=[{"role": "user", "content": suser[:22000]}],
                max_tokens=9000, temperature=0.45, model="groq-llama-3.3-70b", prefer_paid=True) or ""
            syn_en, syn_ko = _split_enko(out)
        except Exception as e:
            log.warning(f"youtube synthesis failed: {str(e)[:100]}")

        def _assemble(syn: str, chans: str) -> str:
            syn = syn or ""
            parts = re.split(r"(?=##\s*3\.)", syn, maxsplit=1)
            overview = parts[0].strip() if parts else ""
            rest = parts[1].strip() if len(parts) > 1 else ""
            return (f"# YouTube Market Analysis\n*{kst_date} (last 24h uploads)*\n\n"
                    f"{overview}\n\n## 2. Channel-by-Channel Analysis\n{chans}\n\n{rest}").strip()

        if chan_en.strip() and syn_en.strip():
            detail_en = _assemble(syn_en, chan_en)
            detail_ko = _assemble(syn_ko or syn_en, chan_ko or chan_en)
    except Exception as e:
        log.warning(f"youtube compose failed: {e}")

    if not detail_en:
        cl = []
        for c in channels:
            cl.append(f"### {c['name']}")
            cl += [f"- {x.get('title','')}" for x in c.get("clips", [])[:4]] or ["- No uploads in 24h"]
        detail_en = (f"# YouTube Market Analysis\n*{kst_date}*\n\n## 1. General Overview\n{sum_en}\n\n"
                     f"## 2. Channel-by-Channel Analysis\n" + "\n".join(cl) + "\n\n"
                     f"## 3. Company-Specific Analysis\nSee channel notes above.\n\n"
                     f"## 4. Catalysts & Schedule (일정매매)\n" + _cat.catalyst_block(catalysts) + "\n\n"
                     f"## 5. Recommendations\n| Stock | Action | Reason |\n|---|---|---|\n"
                     f"| — | HOLD | LLM unavailable — manual review |")
    if not detail_ko:
        detail_ko = detail_en

    src = [{"title": c.get("title", ""), "url": c.get("url", ""), "channel": ch["name"]}
           for ch in channels for c in ch.get("clips", [])]
    return {
        "agent_type": "youtube", "name": "YouTube Market Analysis", "emoji": "📺",
        "status": "ok" if ok_rows else "partial",
        "summary_en": sum_en, "summary_ko": sum_ko,
        "detail_en": detail_en, "detail_ko": detail_ko,
        "table_en": table_en, "table_ko": table_ko,
        "rows": rows,
        "channels": [{"name": c["name"], "video_url": c["video_url"],
                      "n_transcripts": c.get("n_transcripts", 0)} for c in channels],
        "video_sources": src[:40],
        "transcript_stats": transcript_stats,
        "source": "TripleH YouTube Analysis (last-24h uploads + Whisper transcripts: "
                  "Bloomberg TV / WSJ / 한국경제TV / 매일경제 + OHLCV)",
    }
