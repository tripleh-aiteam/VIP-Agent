# VIP Agent Platform — Vercel Frontend Deployment

## Overview

Deploy only the admin dashboard (`apps/admin-dashboard`) to Vercel.
Backend services remain separate (Railway, VPS, etc.).

## Prerequisites

- Vercel account
- Git repository pushed to GitHub/GitLab
- Backend API deployed and accessible via public URL

## Vercel Project Setup

### 1. Import Project

1. Go to [vercel.com/new](https://vercel.com/new)
2. Import your git repository
3. Configure:

| Setting | Value |
|---------|-------|
| **Framework Preset** | Next.js |
| **Root Directory** | `apps/admin-dashboard` |
| **Build Command** | `npm run build` |
| **Output Directory** | `.next` |
| **Install Command** | `npm install` |

### 2. Environment Variables

Add in Vercel Project Settings > Environment Variables:

| Variable | Value | Required |
|----------|-------|----------|
| `NEXT_PUBLIC_API_BASE_URL` | `https://your-backend-api.example.com` | Yes |

**Important:** Use `NEXT_PUBLIC_` prefix — this makes the variable available in the browser.

### 3. Deploy

Click "Deploy". Vercel will:
1. Install dependencies from `apps/admin-dashboard/package.json`
2. Build with `npm run build`
3. Deploy the static + server-rendered pages

## Monorepo Notes

- **Root Directory must be set to `apps/admin-dashboard`**
- The frontend has no imports from outside its own directory
- No cross-folder dependencies that would break Vercel build

## Environment Variables Reference

| Variable | Description | Example |
|----------|-------------|---------|
| `NEXT_PUBLIC_API_BASE_URL` | Backend API base URL | `https://api.vip-agent.example.com` |

## Pages Deployed

| Page | Route |
|------|-------|
| Dashboard | `/` |
| Chat | `/chat` |
| Agents | `/agents` |
| Workflows | `/workflows` |
| Reports | `/reports` |
| Judgement | `/judgement` |
| A2A Monitor | `/a2a` |
| Channels | `/channels` |
| AI Glass | `/ai-glass` |

## CORS

Your backend must allow the Vercel domain in CORS:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or ["https://your-app.vercel.app"]
    ...
)
```

## Local Production Preview

```bash
cd apps/admin-dashboard
npm run build
npm run start
# Opens on http://localhost:3000
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| API calls fail | Check `NEXT_PUBLIC_API_BASE_URL` is set correctly |
| CORS errors | Add Vercel domain to backend CORS origins |
| Build fails | Ensure root directory is `apps/admin-dashboard` |
| Pages 404 | Check all page files exist in `src/app/` |
