# KT Phone System Setup Guide — English Reference

**Project**: VIP / Real-Estate AI Receptionist
**Purpose**: Connect KT 070 number to AI server via SIP trunk + hide 070 from customers via caller-ID display change
**Date**: 2026-05-12

> ⚠️ **Important**: This document is the FULL reference for the entire setup process. Read end-to-end before contacting KT or KCC so you can complete everything **in one call / one submission**. The Korean version ([KT_PHONE_SETUP_KR.md](KT_PHONE_SETUP_KR.md)) contains the actual scripts to use.

---

## 📑 Table of contents

1. **What we're doing — explanation**
2. **Documents you need to prepare** (required + conditional)
3. **PART 1 — KT 070 SIP trunk application**
4. **PART 2 — KCC 발신번호 사전등록 (hiding 070)**
5. **Legal compliance — what you commit to**
6. **Final pre-launch checklist**
7. **Appendix — phone scripts and email templates** (English summaries)

---

# 1. WHAT WE'RE DOING — EXPLANATION

## 1.1 The goal

- Connect your existing KT 070 number to an AI auto-response system (our Asterisk server)
- AI handles customer calls during **off-hours** (evening, weekends, lunch breaks, vacation)
- Customers see your **010 number** instead of 070 on their phone (to avoid spam stigma)
- Runs alongside the KakaoTalk Channel for messaging

## 1.2 Two parallel processes

| | **Part 1: KT SIP Trunk** | **Part 2: KCC Caller-ID Pre-Registration** |
|---|---|---|
| **What** | Permission to connect AI to your 070 | Permission to display 010 as outbound caller-ID |
| **Where** | KT 1577-0114 / biz.kt.com | https://www.msafer.or.kr |
| **Timeline** | 1-3 business days | 2-3 business days |
| **Cost** | Per-minute call rates only | Free |
| **Result** | AI can handle calls | 070 is hidden, only 010 shown |

**Both are required** for the full experience (070 fully hidden). Submit both on the same day to minimize total elapsed time.

## 1.3 Why this approach (vs. alternatives)

| Approach | Customer trust | Effort | Cost |
|---|---|---|---|
| Just 070 (no hiding) | ⭐⭐ Low — spam stigma | Easy | Cheap |
| 070 + 010 display ⭐ | ⭐⭐⭐⭐⭐ Same as 010 | This guide | Cheap |
| 15XX representative number | ⭐⭐⭐⭐⭐ Highest | More paperwork | ~₩100,000/mo |
| KakaoTalk only (no calls) | ⭐⭐⭐⭐ Trusted | Easy | Cheap |
| Skip phone entirely | N/A | None | None — but no voice support |

The 070+010 display approach gives you 010-level trust at 070-level cost. **Used by the majority of Korean SMBs that operate AI receptionists.**

---

\pagebreak

# 2. DOCUMENTS YOU NEED TO PREPARE

## 2.1 Required (all cases)

| # | Document (English) | Korean term | Where to get it | Notes |
|---|---|---|---|---|
| 1 | Business registration certificate | 사업자등록증 | Hometax (www.hometax.go.kr) | PDF, valid within 3 months |
| 2 | Business registration proof | 사업자등록증명원 | Hometax | Same data, different document — both sometimes requested |
| 3 | Representative's ID copy | 대표자 신분증 사본 | — | KR national ID or driver's license |
| 4 | 070 subscription proof | 070 번호 통신서비스 가입 확인서 | KT Bizon (biz.kt.com) → "가입증명서 발급" | Within 3 months |
| 5 | 010 subscription proof | 010 번호 통신서비스 가입 확인서 | Your mobile carrier app (SKT/KT/LGU+) | Within 3 months |

**Critical**: Documents that say "within 3 months" mean the issuance date must be ≤3 months old. Old documents will be rejected.

## 2.2 Conditional (depending on your situation)

| # | Document | When you need it |
|---|---|---|
| 6 | Corporate seal certificate (법인 인감증명서) | If you're a 법인사업자 (corporation). Within 3 months. |
| 7 | Corporate registry extract (법인등기부등본) | If 법인사업자. Within 3 months. |
| 8 | Power of attorney (위임장) | If someone other than the representative is applying |
| 9 | Applicant's ID copy | If applicant is not the representative |
| 10 | Personal → Business name change application (개인 → 법인 명의변경 신청서) | If your 010 is currently under personal name |

## 2.3 If your 010 is a personal number

The KCC registration requires the display number to be **owned by your business**. If your 010 is personal:

### Option 1: Transfer ownership to your business (recommended)
- Call your mobile carrier (SKT 114 / KT 100 / LGU+ 114)
- Request: "Transfer this 010 number from personal to business name (개인 → 법인 명의변경)"
- Bring: 사업자등록증 + transfer consent form (양도양수 동의서) + representative's ID + (if 법인) corporate seal certificate
- Takes: 1-2 business days

### Option 2: If you're a 개인사업자 (sole proprietor)
- The 010 + 070 already under your same personal name → automatically eligible
- No transfer needed, just submit your 사업자등록증 to KCC

---

\pagebreak

# 3. PART 1 — KT 070 SIP TRUNK APPLICATION

## 3.1 What you need to RECEIVE from KT (don't forget anything!)

KT will email these after processing your application. Mark each one received. **If anything is missing, follow up via email to avoid a second phone call.**

### Technical info (needed for Asterisk configuration)

| # | Item | What it looks like |
|---|---|---|
| 1 | SIP server address (host/IP) | e.g. `sip.kt.co.kr` or an IP address |
| 2 | SIP port | Usually `5060` (UDP) |
| 3 | Authentication username | Account-specific string |
| 4 | Authentication password | Secret string — should arrive separately for security |
| 5 | Recommended codec | `G.711 ulaw` or `alaw` (most common) |
| 6 | KT's SIP server IP | For your firewall whitelist |
| 7 | Concurrent channel count | e.g. 2 channels, 5 channels |
| 8 | TLS/SRTP encryption (default vs opt-in) | Plain UDP/RTP is default in KR |

### Administrative info (needed for ongoing management)

| # | Item |
|---|---|
| 9 | Customer / account ID (KT's internal ID for your service) |
| 10 | Service activation date |
| 11 | Monthly base fee |
| 12 | Per-minute rate (KR mobile) |
| 13 | Per-minute rate (KR landline) |
| 14 | International calling availability + rates |
| 15 | Inbound call charges (usually free) |
| 16 | Payment method + billing cycle |
| 17 | Tax invoice (세금계산서) issuance method (typically auto-email) |
| 18 | Technical support phone + email |
| 19 | Support hours (24/7 vs business hours) |
| 20 | SLA / uptime guarantee |
| 21 | Termination policy (minimum contract, penalty fees) |

## 3.2 Questions to ASK KT (yes/no — they should confirm)

| # | Question | Expected answer |
|---|---|---|
| 1 | Is AI auto-response + call recording allowed? | Yes — standard |
| 2 | Are inbound + outbound both supported on the same trunk? | Yes |
| 3 | Any additional permits beyond SIP trunk service? | No (KCC is separate) |
| 4 | Can outbound caller-ID display be changed to our 010? | Yes (after KCC registration) |
| 5 | After KCC approval, does KT auto-sync, or do we need to contact KT separately? | Need to call KT after KCC approval |
| 6 | How do we add/remove channels later? | New application or online portal |
| 7 | If server IP changes, how do we update it? | Email support |
| 8 | Number portability (if we switch carriers)? | Yes, subject to KCC rules |
| 9 | Call recording retention + policies on KT side? | Carrier holds CDRs; your recordings are your own |
| 10 | Future: can we add SMS / fax services? | Usually yes, separate add-on |

## 3.3 Information to GIVE TO KT

Have these ready when you call:

| Item | Your value |
|---|---|
| Business registration certificate | (PDF attached or available) |
| Business registration number | __________________ |
| Company name | __________________ |
| Representative name | __________________ |
| Current KT 070 number | 070-_____-_____ |
| Target 010 number for display | 010-_____-_____ |
| Server's static outbound IP | ___.___.___.___ (or "준비 중 / TBD") |
| Contact person name | __________________ |
| Contact email | __________________ |
| Contact mobile | __________________ |
| Use case description | "AI auto-response system / off-hours real-estate customer service" |
| Expected monthly call volume | ~___ calls/month |

## 3.4 How to apply

### Method A: Phone (fastest) ⭐
- **Number**: 1577-0114 (KT Enterprise Center)
- **Hours**: Mon-Fri 09:00-17:00 KST (avoid 12:00-13:00 lunch)
- **Duration**: 15-30 min
- **Path**: "기업전화 / 비즈 서비스" → SIP trunk specialist

### Method B: KT Bizon Online Portal
- **URL**: https://biz.kt.com (login with business credentials)
- **Menu**: "070 SIP 트렁크 / 기업전화 부가서비스"
- **Advantage**: Written record, less risk of missing items

### Method C: In-person at a KT 직영점
- **Bring**: All documents from §2
- **Best stores**: 강남, 여의도, 광화문 (business-focused branches)

---

\pagebreak

# 4. PART 2 — KCC 발신번호 사전등록 (CALLER-ID PRE-REGISTRATION)

## 4.1 What this is (explanation)

The Korea Communications Commission (KCC) operates a mandatory registration system for any number a business uses as its outbound caller-ID display. We register **both** the 070 (actual SIP trunk) and the 010 (display number) so that outbound calls from our 070 server show as the 010 to recipients.

**This is the legal way to "hide" 070.** Used by every major Korean call center, bank (1588-, 1577-, etc.), insurance company, and delivery service.

## 4.2 Legal basis

| Law | Provision | What it means for us |
|---|---|---|
| Information Communications Network Act §50-8 | Prohibits caller-ID falsification **for fraudulent purposes** | Legitimate business display is allowed — we're fine |
| KCC Pre-Registration System (effective 2015) | Provides the legal mechanism | This IS the path |
| Telecommunications Business Act | Carriers must enforce caller-ID validity | Provides protection against random spoofers |

**Bottom line**: If both numbers belong to the same business (same 사업자등록증), and you register them via KCC, the display change is fully legal and standard.

## 4.3 Documents for KCC submission

Same documents as §2 above. Additionally, KCC may request:

- **Service description** (brief — sample text in §7.2 of the Korean guide)
- **Compliance agreement** (checkbox during the online submission)

## 4.4 Step-by-step online submission

**URL**: **https://www.msafer.or.kr**

1. **Sign up** → choose "사업자 (business)" account type
2. **Verify business** with 사업자등록증 + 사업자등록번호
3. Navigate: **"발신번호 등록"** → **"신규 등록"**
4. **Enter numbers**:
   - 070-XXXX-XXXX (your actual SIP trunk number)
   - 010-XXXX-XXXX (the display number)
5. **Service purpose** (paste this template):
   ```
   AI auto-response system for real estate customer service.
   - Off-hours (evening/weekend) customer inquiry handling
   - Rent reminders for existing tenants
   - First-touch screening with escalation to human boss when needed
   ```
6. **Upload all documents** from §2
7. **Tick compliance agreement** checkbox
8. **Submit** + note the receipt number (접수번호)
9. **Wait 2-3 business days** for approval email
10. **Verify approval** at msafer.or.kr → "등록 현황 조회"

## 4.5 After KCC approval — activate on KT trunk

Once you have the KCC approval email, contact KT (1577-0114) and say:

> "I've completed the KCC pre-registration (receipt number: XXXXXX). Please configure caller-ID display change so outbound calls from our 070 SIP trunk display our registered 010 number."

In Korean:
> "안녕하세요. [회사명] 입니다. KCC 발신번호 사전등록이 승인되었습니다.
> 접수번호는 [XXXXXX] 입니다. 저희 070 SIP trunk 에서 발신할 때, 등록된 010
> 번호가 표시번호로 사용되도록 발신번호 표시 변경 설정 부탁드립니다."

KT activates the setting **the same day** after verifying the KCC approval — typically takes a few hours.

---

\pagebreak

# 5. LEGAL COMPLIANCE — WHAT YOU COMMIT TO

By submitting 발신번호 사전등록, you agree to all of the following. **Violation can result in registration cancellation and fines.**

## 5.1 Five core commitments

| # | Commitment | How we handle it |
|---|---|---|
| 1 | Display only numbers you actually own | ✅ Both 070 and 010 are yours |
| 2 | Use only for the registered purpose (AI receptionist) | ✅ Our system serves only this purpose |
| 3 | Update registry if numbers change | ✅ Your operational responsibility |
| 4 | No unsolicited marketing (telemarketing has separate rules under 정보통신망법) | ✅ We don't do cold outbound to non-customers |
| 5 | AI must disclose itself + recording in the first sentence | ✅ Already in code: `recordingDisclosure` in `vipConfig.voice` |

## 5.2 Call recording law (통신비밀보호법 §3)

- Korean law REQUIRES disclosure before recording a call
- Our AI's first message handles this:
  > "안녕하세요, 트리플H 부동산 AI 비서입니다.
  > 본 통화는 녹음되며 담당자에게 전달됩니다."

  (English: *"Hello, this is Triple-H Real Estate's AI assistant. This call is being recorded and may be shared with a human staff member."*)

- This single sentence at the start of every call = legal recording consent obtained.

## 5.3 Personal data law (개인정보보호법 §15, §17)

- Customer's voice + phone number + call content are all "personal information"
- Collection purpose: "AI response + customer service handling" (declared during registration)
- Retention: **30 days** (our system's default)
- After 30 days: automatically deleted via the retention cron job we built earlier
- Customer right to delete on request: honored immediately (verbal or email request)

## 5.4 What happens if you violate

| Violation | Consequence |
|---|---|
| Display a number you don't own | Registration cancelled, possible §50-8 prosecution |
| Use for spam telemarketing | Up to ₩30M fine (정보통신망법) |
| Record without disclosure | Up to 10 years imprisonment (통신비밀보호법) — RARE for businesses with proper disclosure |
| Keep personal data beyond stated retention | Up to ₩20M fine per case (PIPA) |

**Important**: With our system's built-in disclosures and 30-day retention, **you're already compliant**. Just don't manually disable these features and you're safe.

---

\pagebreak

# 6. FINAL CHECKLIST

## 6.1 Before you contact KT (Day 0 preparation)

- [ ] 사업자등록증 PDF (within 3 months) downloaded
- [ ] 사업자등록증명원 PDF (within 3 months)
- [ ] 사업자등록번호 memorized or written
- [ ] 회사명 + 대표자명 confirmed
- [ ] Current KT 070 number written
- [ ] Current 010 number written
- [ ] 대표자 신분증 (KR ID) copy scanned
- [ ] (If 법인) 법인 인감증명서 + 등기부등본 (within 3 months)
- [ ] 070 통신서비스 가입 확인서 (within 3 months)
- [ ] 010 통신서비스 가입 확인서 (within 3 months)
- [ ] (If 010 is personal) name change to business completed
- [ ] Server's static IP secured (or cloud server signed up)
- [ ] This guide printed or open on screen

## 6.2 Day 1 — submission day

- [ ] **Morning**: Submit at msafer.or.kr (30 min)
- [ ] **Morning**: Note the 접수번호 (receipt number)
- [ ] **Afternoon**: Call KT 1577-0114 → SIP trunk application
- [ ] During the call: Go through every item in §3.1 and §3.2
- [ ] Confirm what KT will email you and the expected timeline
- [ ] End of day: Update dev team with status

## 6.3 Days 2-3 — waiting

- [ ] Watch for KCC email
- [ ] Watch for KT email
- [ ] In parallel: prepare server (static IP, cloud account) if not done

## 6.4 Days 4-5 — credentials received

- [ ] **Forward KCC approval email** to dev team
- [ ] **Forward KT SIP credentials email** to dev team (the email is everything we need)
- [ ] Call KT again with KCC receipt number → activate caller-ID display
- [ ] KT confirms activation same day
- [ ] Be available for ~30 min smoke test with dev team

## 6.5 What to send the dev team

Paste this template (English version) to me when ready:

```
Subject: KT Phone Setup — Credentials Ready

Hi,

Both applications completed. Forwarding the credentials below.

=== KCC 발신번호 사전등록 ===
- Status: APPROVED ✅
- Receipt number: XXXXXX
- Approved on: YYYY-MM-DD
- Registered numbers:
  - 070-XXXX-XXXX (actual SIP trunk)
  - 010-XXXX-XXXX (display caller-ID)

=== KT SIP Trunk ===
- SIP host: ______________
- SIP port: ______________ (usually 5060)
- Username: ______________
- Password: ______________ (sent separately for security)
- Codec: G.711 ulaw / alaw
- Concurrent channels: __
- KT SIP server IP (for whitelist): ______________
- Monthly fee: ______________ KRW
- Per-minute rate (KR mobile): ______________ KRW

=== Server side ===
- Static IP: ______________
- Asterisk location: cloud / office
- Available for smoke test: [time slots]

KT caller-ID display activation: confirmed on YYYY-MM-DD

Please configure Asterisk and let me know when ready to test.

Thanks,
[Your name]
```

---

\pagebreak

# 7. APPENDIX — REFERENCE SCRIPTS

> The actual phone scripts to USE during the KT call are in the Korean version of this guide ([KT_PHONE_SETUP_KR.md](KT_PHONE_SETUP_KR.md), §7). This English version is for your understanding of the conversation flow.

## 7.1 KT phone call flow (English summary)

1. **Greeting**: "I want to apply for SIP trunk service on our company's 070 number. Please connect me to the business voice / SIP team."

2. **Once connected to a SIP specialist**:
   - Introduce: company name, briefly explain you're integrating an AI auto-response system
   - Application items:
     - SIP trunk on existing 070
     - Caller-ID display change (010 → display)
     - Concurrent channels: 2-5 to start
   - Information to request (full list in §3.1):
     - SIP credentials (host, port, username, password, codec)
     - KT SIP server IP for our firewall whitelist
     - Channel count, monthly fee, per-minute rates
     - Payment method, tax invoice issuance
     - Technical support contact + hours
     - Activation date, channel scaling process, termination policy
   - Confirmations (full list in §3.2):
     - AI auto-response + call recording allowed?
     - Inbound + outbound on same trunk?
     - After KCC approval, does KT auto-sync or need separate call?

3. **Before ending the call**: Reconfirm timeline, your email address, what KT will send

## 7.2 KCC submission form purpose statement (paste into 이용 목적)

```
[Company name] is applying for caller-ID pre-registration for our
AI auto-response system.

Service purpose:
- Off-hours (evening, weekend, lunch) customer inquiry handling
- Real-estate rental consulting via AI
- Rent reminders for existing tenants

Registered numbers:
- 070-XXXX-XXXX: Actual SIP trunk operating number (KT)
- 010-XXXX-XXXX: Display caller-ID (shown to recipients)

Compliance:
- Both numbers under same business registration
- AI's first sentence discloses auto-response status + recording
- Compliant with: 정보통신망법 §50-8, 통신비밀보호법 §3,
  개인정보보호법 §15 / §17
- Personal data retention: 30 days automatic deletion

Contact: [Name], [Email], [Phone]
```

## 7.3 If KT asks technical follow-up questions

| KT question | Your answer |
|---|---|
| "What exactly is your AI auto-response system?" | "An open-source stack: Asterisk + Whisper STT + LLM + TTS for voice processing." |
| "Where are call recordings stored?" | "Our Supabase Storage with encryption. Auto-deleted after 30 days." |
| "Is the system government-certified?" | "Self-built SMB system. Government certification not required at our scale; we can pursue if needed in the future." |
| "What if monthly call volume exceeds expectations?" | "Estimating ~500 calls/month. Will request additional channels if we exceed." |
| "Do you have a privacy policy / 개인정보처리방침?" | "Yes, will share separately. Our retention is 30 days, deletion on customer request." |

---

# 📞 QUICK REFERENCE

| Agency | Phone | URL | Purpose |
|---|---|---|---|
| **KT Enterprise Center** | **1577-0114** | https://biz.kt.com | SIP trunk + caller-ID display |
| **KCC / KISA** | 118 (general) | **https://www.msafer.or.kr** | 발신번호 사전등록 |
| **Hometax (NTS)** | 126 | https://www.hometax.go.kr | Business reg. cert issuance |
| **SKT** (if 010 is SKT) | 114 | https://www.tworld.co.kr | 010 ownership transfer if needed |
| **LGU+** (if 010 is LGU+) | 114 | https://www.lguplus.com | Same |

**Best contact times**: Weekdays 10:00-11:30, 14:00-16:00 KST (avoid lunch + end-of-day rushes)

---

# 🎯 KEY TAKEAWAYS

1. **Two processes, one day** — submit KCC + call KT on the same day
2. **Two emails to forward to dev team** — one from KCC (approval), one from KT (credentials)
3. **One additional KT call after KCC approval** — to activate the caller-ID display
4. **Total elapsed time**: ~5 business days from start to working phone bot
5. **Total cost**: Zero setup fees, only per-minute call rates ongoing

**You're not doing anything tricky or shady** — this is the standard, government-blessed, mainstream way every Korean SMB sets up an AI receptionist. The legal framework explicitly exists for this purpose.

---

**Document version**: 1.0 — 2026-05-12
**Related files**:
- [`KT_PHONE_SETUP_KR.md`](KT_PHONE_SETUP_KR.md) — Korean version (the scripts to actually USE during the call)
- [`README.md`](README.md) — Asterisk infrastructure README
- [`pjsip.conf.template`](pjsip.conf.template) — Asterisk SIP configuration template
- [`extensions.conf.template`](extensions.conf.template) — Asterisk dialplan template

**Maintained by**: VIP AI Platform team
