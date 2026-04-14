# VIP Agent Platform — Vercel Deployment Checklist

## 1. What Gets Deployed to Vercel

- `apps/admin-dashboard` (Next.js frontend only)
- 9 pages: Dashboard, Chat, Agents, Workflows, Reports, Judgement, A2A, Channels, AI Glass

## 2. What Stays Outside Vercel

| Component | Host Separately |
|-----------|----------------|
| Orchestrator API (FastAPI) | Railway / VPS / AWS |
| Mock agents (3 services) | Same server as backend |
| PostgreSQL | Supabase |
| Redis | Railway / VPS |
| APScheduler | Runs inside orchestrator |

## 3. Vercel Project Name

```
vip-agent-dashboard
```

## 4. Root Directory

```
apps/admin-dashboard
```

## 5. Framework

```
Next.js  (auto-detected)
```

## 6. Environment Variables

Add in **Vercel > Project Settings > Environment Variables**:

| Variable | Value | Example |
|----------|-------|---------|
| `NEXT_PUBLIC_API_BASE_URL` | Your backend URL | `https://api.vip-agent.example.com` |

That's it — only 1 env var needed.

## 7. Preview Deployment

Happens automatically on every push to a non-main branch:

```bash
git checkout -b feature/my-change
# make changes
git add -A && git commit -m "my change"
git push origin feature/my-change
# Vercel auto-deploys preview at: vip-agent-dashboard-xxx.vercel.app
```

## 8. Production Deployment

Happens automatically on push to `main`:

```bash
git checkout main
git merge feature/my-change
git push origin main
# Vercel auto-deploys production at: vip-agent-dashboard.vercel.app
```

Or manually: Vercel Dashboard > Deployments > Redeploy.

## 9. Common Issues

### Missing env var
**Symptom:** Pages load but show no data, console shows fetch errors.
**Fix:**
```
Vercel > Project Settings > Environment Variables
Add: NEXT_PUBLIC_API_BASE_URL = https://your-backend-url
Redeploy.
```

### Wrong root directory
**Symptom:** Build fails with "next: command not found" or missing package.json.
**Fix:**
```
Vercel > Project Settings > General > Root Directory
Set to: apps/admin-dashboard
```

### CORS error
**Symptom:** Console shows "blocked by CORS policy".
**Fix:** On your backend server, set:
```env
CORS_ALLOWED_ORIGINS=https://vip-agent-dashboard.vercel.app
```
Restart backend.

### Build failure
**Symptom:** Vercel build log shows TypeScript or import errors.
**Fix:** Run locally first:
```bash
cd apps/admin-dashboard
npm run check:deploy
```
Fix any errors, push again.

### Wrong API base URL
**Symptom:** Pages load but API calls go to localhost or wrong server.
**Fix:** Check the env var value has no trailing slash and correct protocol:
```
✅ https://api.vip-agent.example.com
❌ https://api.vip-agent.example.com/
❌ http://localhost:8000
```

## 10. Verification Checklist After Deployment

Open your Vercel URL and check:

- [ ] `/` — Dashboard loads, shows agent count and run stats
- [ ] `/chat` — Chat page loads, can create session
- [ ] `/agents` — Agent cards appear with status dots
- [ ] `/workflows` — Schedule dropdowns open with schedules
- [ ] `/reports` — Report list loads, compose buttons work
- [ ] `/judgement` — Judgement cases table loads
- [ ] `/a2a` — A2A messages table loads
- [ ] `/channels` — Channel tabs work, Telegram simulator loads
- [ ] `/ai-glass` — AI Glass sessions and stats load
- [ ] Browser console — no CORS errors, no 404s on API calls
