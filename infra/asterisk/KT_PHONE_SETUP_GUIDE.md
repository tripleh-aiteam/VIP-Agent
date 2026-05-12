# KT Phone Setup Guide — Bilingual Reference

**Project**: VIP / Real-Estate AI Receptionist
**Purpose**: Complete reference for setting up the phone side of the chatbot — getting permission to use your 070 with our AI server (Part 1) and configuring caller-ID so customers only see your 010 (Part 2).

**Print and keep with you** when calling KT or submitting forms.

---

## 📑 Table of contents

1. **Overview** — two-step process
2. **PART 1** — KT 070 SIP trunk (permission to connect AI to phone)
3. **PART 2** — KCC 발신번호 사전등록 (hiding 070 from customers)
4. **Combined timeline**
5. **Final checklist + what to send the dev team**
6. **Appendix** — phone scripts + email templates

---

# 1. OVERVIEW — 개요

We need to do TWO separate things at TWO different agencies. They run in parallel — you can submit both on the same day.

| | **Part 1: KT SIP Trunk** | **Part 2: KCC 발신번호 사전등록** |
|---|---|---|
| **Purpose (EN)** | Permission to connect our AI server to your existing KT 070 number | Permission to display 010 as outbound caller-ID (hide 070) |
| **목적 (KR)** | KT 070 번호를 AI 서버에 연결하기 위한 권한 | 발신 시 070 대신 010 번호가 표시되도록 변경 |
| **Agency** | KT (your telecom carrier) | KCC / 한국인터넷진흥원 (KISA) |
| **Where** | Phone: 1577-0114, or business portal | Online: https://www.msafer.or.kr |
| **Cost** | Per-minute call rates only (no setup fee) | 무료 (free) |
| **Timeline** | 1-3 business days | 2-3 business days |

**Why both?**
- Part 1 alone: bot works, but every outbound call displays "070-XXXX-XXXX" → customers may reject as spam
- Part 2 alone: pointless without Part 1 — nothing to display caller-ID for
- **Both together**: AI handles calls + customers see your trusted 010 → ideal

---

\pagebreak

# 2. PART 1 — KT 070 SIP TRUNK

## 2.1 What you're requesting (English / Korean)

**English**: A SIP trunk service on your existing 070 number that lets our AI server (Asterisk) receive incoming calls and place outbound calls programmatically. Standard KT business service, used by thousands of KR companies.

**한국어**: 저희 회사의 기존 070 번호에 SIP trunk 서비스를 신청하여, AI 자동응답 서버 (Asterisk)가 통화를 수신하고 발신할 수 있도록 합니다. KT의 표준 기업 서비스입니다.

## 2.2 Documents to prepare (서류 준비)

| # | English | Korean |
|---|---|---|
| 1 | Business registration certificate (PDF) | 사업자등록증 (PDF, 1부) |
| 2 | Business registration number | 사업자등록번호 |
| 3 | Company name + representative name | 회사명 + 대표자명 |
| 4 | Current 070 number being upgraded to SIP | 현재 사용 중인 070 번호 (SIP 연동 대상) |
| 5 | Outbound static IP of your server (where Asterisk will run) | 저희 서버의 고정 외부 IP 주소 |
| 6 | Contact person: name + email + mobile | 담당자: 이름 + 이메일 + 휴대폰 |
| 7 | Use case description (one line) | 이용 용도 (한 문장) |
| 8 | Expected monthly call volume estimate | 예상 월간 통화량 |
| 9 | Representative's ID copy | 대표자 신분증 사본 |

**Use case wording** (use this exact phrase — proven to get faster approval):

- **EN**: "AI auto-response system / AI receptionist for off-hours customer service"
- **KR**: "AI 자동응답 시스템 운영 / 비대면 상담용 AI 비서"

## 2.3 How to apply

### Option A: Phone (recommended — fastest)
- **Number**: 1577-0114
- **Press**: 기업전화 / 비즈 서비스 관련 메뉴 → SIP trunk 담당자 연결
- **Best time**: Mon–Fri 09:00–17:00 KST
- **Estimated duration**: 15-30 min on the phone

### Option B: KT Bizon Portal (online)
- **URL**: https://biz.kt.com (login with business credentials)
- Find: "070 SIP 트렁크 / 기업전화 부가서비스 신청"
- Upload documents, fill in service details

### Option C: In-person at a KT 직영점 (direct store)
- Bring all documents from §2.2
- Some stores have dedicated business desks (대구·서울 강남·여의도 등)

## 2.4 Information to ASK FOR (받아야 할 정보)

When KT processes your application, they will email you the following. **Confirm you receive all of these:**

| # | English | Korean |
|---|---|---|
| 1 | SIP server address (host/IP) | SIP 서버 주소 (host/IP) |
| 2 | SIP port (usually 5060) | SIP 포트 (보통 5060) |
| 3 | Authentication username | 인증용 사용자 ID |
| 4 | Authentication password | 인증용 비밀번호 |
| 5 | Recommended codec (G.711 ulaw or alaw) | 권장 코덱 (G.711 ulaw 또는 alaw) |
| 6 | KT's SIP server IP (for our firewall whitelist) | KT SIP 서버 IP (방화벽 허용 목록) |
| 7 | Concurrent call channel limit | 동시 통화 채널 수 |
| 8 | Monthly fee + per-minute rate breakdown | 월 기본료 + 분당 통화료 |

## 2.5 Questions to CONFIRM (확인할 사항)

| # | English | Korean | Expected answer |
|---|---|---|---|
| 1 | Is AI auto-response + call recording allowed? | AI 자동응답 + 통화 녹음 가능한가요? | Yes — standard |
| 2 | Are inbound + outbound both supported on the same trunk? | 인바운드 + 아웃바운드 모두 가능한가요? | Yes |
| 3 | Any extra permits beyond SIP trunk service? | SIP trunk 외 추가 인허가 필요한가요? | No (KCC registration handled separately) |
| 4 | TLS/SRTP encryption by default? | TLS/SRTP 암호화 기본 적용? | Optional — request if needed |
| 5 | Approval timeline? | 승인 소요일은? | 1-3 business days |

## 2.6 Cost expectation (예상 비용)

| Item | Korean | Typical cost |
|---|---|---|
| Monthly base fee per trunk | 월 기본료 | ~₩10,000-30,000 |
| Per-minute outbound (KR mobile) | 분당 통화료 (국내 휴대폰) | ~₩40-60 |
| Per-minute outbound (KR landline) | 분당 통화료 (국내 시내) | ~₩30-50 |
| Inbound calls | 인바운드 (수신) | ₩0 (free) |
| Setup fee | 설치비 | ₩0 (no setup fee for SIP trunk) |

---

\pagebreak

# 3. PART 2 — KCC 발신번호 사전등록 (CALLER-ID DISPLAY CHANGE)

## 3.1 What this is (English / Korean)

**English**: A government-mandated registration that authorizes you to display a specific phone number as your outbound caller-ID. In our case, we register both the 070 (actual trunk) AND the 010 (display number), so outbound calls from our 070 server show your 010 to recipients. **This is the legal way to "hide" 070.** Same mechanism used by every major Korean call center, bank (1588-), and delivery app.

**한국어**: 정부 (방송통신위원회 / KCC) 가 운영하는 발신번호 사전등록제. 출력 발신번호를 지정된 번호로 변경하기 위한 법적 절차입니다. 저희는 070 (실제 트렁크) 와 010 (표시번호) 둘 다 등록하여, 070에서 발신되는 통화가 수신자에게는 010 번호로 표시되도록 합니다. **이것이 070을 합법적으로 "숨기는" 방법**이며, 한국의 모든 콜센터·은행·배달 앱이 사용하는 표준 절차입니다.

## 3.2 Legal basis (법적 근거)

- **정보통신망법 §50-8**: Prohibits caller-ID spoofing **for fraudulent purposes only**. Legitimate business display is allowed.
- **발신번호 사전등록제** (KCC, 2015 시행): Provides the legal mechanism for businesses to register and use display numbers.
- **전기통신사업법**: Carriers must enforce caller-ID validity against the KCC registry.

**Bottom line**: Owning both numbers under the same business registration = fully legal to display either as outbound caller-ID after KCC registration.

## 3.3 Documents to prepare (서류 준비)

| # | English | Korean |
|---|---|---|
| 1 | Business registration certificate (PDF) | 사업자등록증 (PDF, 1부) |
| 2 | Subscription proof — 010 number | 010 번호 통신서비스 가입 확인서 |
| 3 | Subscription proof — 070 number | 070 번호 통신서비스 가입 확인서 |
| 4 | Representative's ID copy | 대표자 신분증 사본 |
| 5 | Corporate seal scan (법인 only) | 법인 인감 (법인사업자에 한함) |
| 6 | Service description | 서비스 이용 목적 설명 |

### How to get the 가입 확인서 (subscription proof):

- **For 010**: Login to your mobile carrier (SKT/KT/LGU+) → "통신서비스 가입증명서" download. Or visit a 지점.
- **For 070**: Login to KT Bizon (biz.kt.com) → "가입증명서 발급" menu.

Both should show: your company name + the number + service start date.

## 3.4 If your 010 is a PERSONAL mobile (개인)

If the 010 you want to use as display is **currently registered as personal** (not under your 사업자), you have two options:

**Option A — Transfer ownership to business** (recommended):
- Call your mobile carrier (SKT 114 / KT 100 / LGU+ 114)
- Request: "개인 → 법인/사업자 명의 변경 신청"
- Bring: 사업자등록증 + 양도양수 동의서 (if changing names)
- Takes: 1-2 days

**Option B — Register both numbers under your personal 개인사업자 ID**:
- Only works if you're 개인사업자 (sole proprietor), not 법인
- Same person owns both → automatically eligible

Either path → 010 ends up linkable to your 사업자등록증 → KCC accepts the registration.

## 3.5 How to apply

**URL**: **https://www.msafer.or.kr** (KCC's official site)

### Step-by-step:

1. **회원가입** (sign up) — choose 사업자 (business) account type
2. **사업자 인증** — verify with 사업자등록증
3. Menu navigation: **"발신번호 등록"** → **"신규 등록"**
4. Fill in:
   - Number: 070-XXXX-XXXX (actual SIP trunk)
   - Number: 010-XXXX-XXXX (display number)
   - 사용 용도: "AI 자동응답 시스템 / 부동산 임대 상담"
5. **Upload all documents** from §3.3
6. **Submit** and note the receipt number (접수번호)
7. **Wait 2-3 business days** for email approval

### After approval:

- The KCC notifies KT automatically via the carrier registry sync
- You can verify on msafer.or.kr → "등록 현황 조회"
- Then proceed to §3.6 (KT activation)

## 3.6 Activating caller-ID display on the KT trunk

After KCC approves, you need to tell KT to actually USE the 010 as the display number on outbound calls from your 070 trunk.

**Phone call to KT** (1577-0114):

> "발신번호 사전등록 (KCC) 완료했습니다. 접수번호는 [XXXXXX] 입니다.
> 저희 070 SIP trunk에서 발신할 때, 등록된 010 번호가 표시번호로 사용되도록 발신번호 표시 변경 설정 부탁드립니다."

**English meaning**:
> "I've completed the KCC pre-registration (receipt number: XXXXXX). Please configure caller-ID display change so outbound calls from our 070 SIP trunk display our registered 010 number."

KT then activates the setting on their side — typically **same day** after they verify the KCC approval.

## 3.7 What you commit to (compliance)

By submitting 발신번호 사전등록, you agree to:

| # | Commitment |
|---|---|
| 1 | Display only numbers you actually own under your business |
| 2 | Use the display for the registered purpose (AI receptionist) only |
| 3 | Update the registry if numbers change |
| 4 | Not use for unsolicited telemarketing (separate rules under 정보통신망법) |
| 5 | Ensure your AI's first sentence discloses it's automated + recorded |

We've already built #5 into the AI's prompt (`recordingDisclosure` in `vipConfig.voice` — Korean PIPA compliance). Other 4 are operational — easy to follow.

## 3.8 Cost (비용)

- KCC registration: **무료 (free)**
- KT caller-ID display setting: **무료 (no extra charge)**
- Total Part 2 cost: **₩0**

---

\pagebreak

# 4. COMBINED TIMELINE — 통합 일정

```
                  Day 1        Day 2-3       Day 4         Day 5
                  ─────────    ─────────     ─────────     ─────────
KT SIP TRUNK      📞 Apply →   ⏳ Processing → ✉ Receive →
                              (1-3 days)      credentials
                                              by email

KCC 발신번호       🌐 Submit →  ⏳ Processing → ✅ Approved →  📞 Call KT
사전등록           (msafer.or                  (email)         to activate
                  .kr, 30 min)                                display

DEV               🟢 Build      🟢 More       🟢 Wait for    🟢 Configure
                  DB schema,    code           credentials    Asterisk
                  conversation  (parallel)
                  service
```

## Optimal day-by-day plan

| Day | Your tasks | Dev tasks |
|---|---|---|
| **Day 1** (Mon) | • Submit at msafer.or.kr (30 min)<br>• Call KT 1577-0114 for SIP trunk (15-30 min) | Phase A8-A10 (DB + conversation service + mode detector) |
| **Day 2-3** | (waiting on KCC + KT) | Phase A6-A7, A11-A14 (Kakao client + webhook + REST + WS) |
| **Day 4** | • Receive KCC approval email<br>• Receive KT SIP credentials email<br>• Forward both to dev team | Phase A15-A17 (voice msg + image + replace mocks) |
| **Day 5** | • Call KT to activate caller-ID display<br>• Smoke test together with dev | Configure Asterisk + smoke test |

**Result by end of Day 5**: Working AI receptionist on your 070 number, displaying 010 to customers, with KakaoTalk integration alongside.

---

\pagebreak

# 5. FINAL CHECKLIST

## ✅ Before you start (Day 0)

- [ ] 사업자등록증 PDF on your laptop/phone
- [ ] 사업자등록번호 memorized
- [ ] 회사명, 대표자명
- [ ] Current KT 070 number written down
- [ ] Current 010 number written down
- [ ] 대표자 신분증 사본 (scanned)
- [ ] Decided on cloud server (or have a friend's static IP) for Asterisk
- [ ] 010 number is **business-registered** (or planning to transfer it)

## ✅ Day 1 actions

- [ ] Submit 발신번호 사전등록 at **msafer.or.kr** (30 min)
- [ ] Note the 접수번호 (receipt number)
- [ ] Call **KT 1577-0114** → request SIP trunk for 070 (15-30 min)
- [ ] Send dev team a quick update: "Both applications submitted, awaiting approval"

## ✅ Day 4-5 actions

- [ ] **Forward KCC approval email** to dev team
- [ ] **Forward KT SIP credentials email** to dev team (contains: SIP host, port, username, password, codec)
- [ ] Call KT again to activate caller-ID display on the trunk
- [ ] Be available for ~30 min smoke test with dev team

## ✅ What to send the dev team (when ready)

Paste this template into Slack/email/KakaoTalk:

```
=== KT Phone Setup — Credentials Ready ===

KCC 발신번호 사전등록:
- Receipt #: XXXXXX
- Approval date: YYYY-MM-DD
- Status: APPROVED
- Display number registered: 010-XXXX-XXXX

KT SIP Trunk:
- SIP host: __________
- SIP port: __________ (usually 5060)
- Username: __________
- Password: __________ (or sent securely separately)
- Codec: G.711 ulaw / alaw
- Concurrent channels: __
- KT SIP server IP (for whitelist): __________

Server side (we provide):
- Static IP: __________ (server where Asterisk runs)
- Caller-ID display activation: confirmed by KT YYYY-MM-DD
```

---

\pagebreak

# 6. APPENDIX — TEMPLATES

## 6.1 Phone call script — KT 1577-0114

**Greeting**:
> "안녕하세요. 저희 회사 070 번호에 SIP trunk 서비스 신청 문의입니다. 기업전화 담당자 부탁드립니다."

**Once connected to SIP specialist**:
> "안녕하세요. [회사명] 입니다. 저희 회사의 기존 070 번호에 SIP trunk 연결 신청을 하고 싶습니다.
> AI 자동응답 시스템과 연동하여 비대면 상담 용도로 사용할 예정입니다.
>
> 필요한 정보:
> 1. SIP 서버 주소, 포트, 인증 ID/PW
> 2. 권장 코덱 정보
> 3. 동시 통화 채널 수
> 4. 월 기본료 + 분당 통화료
> 5. 저희 서버 고정 IP 화이트리스트 등록
>
> 추가로 발신번호 표시 변경 (010 번호로 표시) 가능한지 확인 부탁드립니다.
> KCC 발신번호 사전등록은 별도로 진행하겠습니다.
>
> 신청 절차 + 예상 소요일 안내 부탁드립니다."

## 6.2 Email template — KCC submission notes

If the msafer.or.kr form has an "additional notes" field, paste this:

```
[회사명] - AI 자동응답 시스템 운영을 위한 발신번호 사전등록 신청

운영 목적:
- 부동산 임대 상담을 위한 AI 자동응답 시스템 운영
- 비영업시간 (저녁/주말) 고객 문의 자동 응대
- 임대료 알림 등 기존 고객 대상 정기 안내

등록 희망 번호:
- 070-XXXX-XXXX: 실제 SIP trunk (KT)
- 010-XXXX-XXXX: 표시 발신번호 (수신자에게 표시됨)

준법 사항:
- 두 번호 모두 동일 사업자등록증 하에 운영
- 통화 첫 문장에 AI 자동응답임을 고지
- 통화 녹음 사실 및 사용 목적 안내 진행
- 정보통신망법 §50-8 준수 확인

문의: [담당자 이름], [이메일], [전화]
```

## 6.3 Email template — to send dev team

```
Subject: KT Phone Setup — Credentials Ready

Hi [Dev],

Both applications completed. Forwarding the credentials below.

=== KCC 발신번호 사전등록 ===
- Status: APPROVED ✅
- Receipt: XXXXXX
- Approved on: YYYY-MM-DD
- Numbers registered:
  - 070-XXXX-XXXX (SIP trunk)
  - 010-XXXX-XXXX (display caller-ID)

=== KT SIP Trunk ===
[Forwarding KT's email with credentials]

=== Server Info ===
- Static IP: ______________
- Server location: Cloud / Office
- Available for smoke test: [time slots]

Please configure Asterisk + let me know when ready to test.

Thanks,
[Your name]
```

---

# 📞 Quick reference — phone numbers

| Agency | Phone | URL | Purpose |
|---|---|---|---|
| **KT 기업고객센터** | **1577-0114** | https://biz.kt.com | SIP trunk + caller-ID activation |
| **KCC / KISA** | **118** (general) | **https://www.msafer.or.kr** | 발신번호 사전등록 |
| **SKT 고객센터** (if 010 is SKT) | 114 | https://www.tworld.co.kr | 010 carrier coordination |
| **LGU+ 고객센터** (if 010 is LGU+) | 114 | https://www.lguplus.com | 010 carrier coordination |

---

**Document version**: 1.0 — 2026-05-12
**Maintained by**: VIP AI Platform team
**Project**: `vip-ai-platform/infra/asterisk/`
