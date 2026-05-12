# Asterisk SIP Edge — KT 070 → Voice Agent

This directory contains the Asterisk configuration that bridges your KT
070 SIP trunk to the Python voice agent pipeline.

## Architecture

```
┌─────────────────────┐
│ Real caller (010-…) │
└─────────────────────┘
           │
           ▼  PSTN / mobile network
┌──────────────────────────────────────────┐
│ KT carrier (your 070 number lives here)  │
└──────────────────────────────────────────┘
           │
           ▼  SIP/UDP 5060  (TLS optional: 5061)
┌──────────────────────────────────────────┐
│ Asterisk (this directory's config)       │
│  - Authenticates with KT SIP credentials  │
│  - Handles call setup / teardown          │
│  - Negotiates G.711 μ-law codec           │
└──────────────────────────────────────────┘
           │
           ▼  AudioSocket (TCP, raw PCM 8kHz)
┌──────────────────────────────────────────┐
│ services/voice_pipeline.py               │
│  - Receives caller audio frames           │
│  - Streams to Whisper (STT)               │
│  - Streams to local LLM (Ollama)          │
│  - Streams to MeloTTS (synthesizes reply) │
│  - Sends synthesized audio back           │
└──────────────────────────────────────────┘
```

## Why Asterisk

Real-time SIP + RTP handling in pure Python is fragile (jitter, NAT,
codec negotiation, packet loss). Asterisk is the industry-standard SIP
edge used in millions of production deployments. Its **AudioSocket**
extension forwards raw 8kHz PCM audio over a simple TCP socket to our
Python service — so our Python code never touches SIP and only deals
with PCM-in / PCM-out.

## What you need from KT (admin task)

**Call KT (1577-0114 → business voice support / 기업전화)** and request:

> 안녕하세요, 저희 회사 070 번호에 SIP trunk 연결을 신청하고 싶습니다.
> AI 자동응답 시스템과 연동할 예정입니다.
>
> 다음 정보가 필요합니다:
> 1. SIP 서버 주소 (host/IP)
> 2. SIP 포트 (보통 5060)
> 3. 인증용 username / password
> 4. 권장 코덱 (G.711 ulaw / alaw 중 어느 것)
> 5. 발신측 IP 화이트리스트 등록 (저희 서버 IP)
>
> 통화 녹음 및 자동응답 시스템 운영에 대한 허가가 필요한지도 확인 부탁드립니다.

**Translation:**

> Hello, I want to apply for SIP trunk connection for our company's 070 number.
> We will integrate with an AI auto-response system.
>
> I need the following information:
> 1. SIP server address (host/IP)
> 2. SIP port (usually 5060)
> 3. Authentication username / password
> 4. Recommended codec (G.711 ulaw or alaw)
> 5. IP whitelist registration for our server's outbound IP
>
> Please also confirm whether call recording and auto-response system
> operation requires any additional permits.

KT typically takes **1-3 business days** to process and will email you
back the credentials. You'll likely need to provide:

- Business registration number (사업자등록번호)
- A static outbound IP for your server (so they can whitelist it)
- Use case description (mentioning "AI 자동응답 시스템" is fine; they have
  precedent for this — many call-center deployments use the same pattern)

## Configuration files

This directory will contain (created as the build progresses):

```
asterisk/
├── README.md                  ← this file
├── pjsip.conf.template        ← SIP trunk credentials (KT-facing)
├── extensions.conf.template   ← Dialplan: route inbound → AudioSocket
├── audiosocket.conf.template  ← AudioSocket bind address + port
└── docker-compose.yml         ← Containerized Asterisk for easy deploy
```

Replace `_REPLACE_ME_` placeholders with values from KT's email.

## Once KT credentials arrive

1. Copy each `*.template` file to `*.conf` (without the `.template` suffix)
2. Fill in `_REPLACE_ME_` values from KT's email
3. Run `docker compose up -d` from this directory
4. Verify SIP registration: `docker exec asterisk asterisk -rx "pjsip show registrations"`
5. Test inbound: call your 070 from your phone — Asterisk logs should show INVITE
6. Test outbound (after 발신번호 사전등록 with KCC, 2-3 days): use the dashboard's
   "Call now" button

## Why we still need a Korean carrier (even though everything else is self-hosted)

PSTN (Public Switched Telephone Network) is licensed infrastructure —
you cannot self-host the connection between real phones and the internet.
Some carrier always sits in the middle. KT is already your existing
carrier for the 070 number, so we use them.

The only "recurring fee" you can't escape is KT's per-minute rate
(~₩30-50/min) + 070 monthly rental (~₩5,000-15,000). Everything else
in the stack (Asterisk, Whisper, Ollama, MeloTTS) is free and runs on
your own server.
