# VIP Agent Platform — Vercel Project Setup

## Project Info

| Field | Value |
|-------|-------|
| **Recommended Name** | `vip-agent-dashboard` |
| **Root Directory** | `apps/admin-dashboard` |
| **Framework** | Next.js (auto-detected) |
| **Build Command** | `npm run build` |
| **Install Command** | `npm install` |
| **Output** | Auto (Vercel handles `.next`) |
| **Node Version** | 20.x |

## Step-by-Step Setup

### 1. Push to GitHub

```bash
cd vip-ai-platform
git add -A
git commit -m "Prepare for Vercel deployment"
git remote add origin https://github.com/YOUR_USER/vip-ai-platform.git
git push -u origin main
```

### 2. Import on Vercel

1. Go to https://vercel.com/new
2. Click **Import** next to your repository
3. Set **Project Name**: `vip-agent-dashboard`
4. Set **Root Directory**: `apps/admin-dashboard`
5. Framework will auto-detect as **Next.js**
6. Click **Deploy**

### 3. Environment Variables

Go to **Project Settings > Environment Variables** and add:

| Name | Value | Environments |
|------|-------|-------------|
| `NEXT_PUBLIC_API_BASE_URL` | `https://your-backend.example.com` | Production, Preview |

### 4. Deploy

Click **Deploy**. Vercel will:
1. Clone your monorepo
2. Navigate to `apps/admin-dashboard`
3. Run `npm install`
4. Run `npm run build`
5. Serve the built Next.js app

## Preview vs Production

| Type | Branch | URL |
|------|--------|-----|
| Production | `main` | `vip-agent-dashboard.vercel.app` |
| Preview | Any other branch | `vip-agent-dashboard-xxx.vercel.app` |

Both environments use the same env vars unless you set per-environment values in Vercel.

## Pages Deployed

| Route | Page |
|-------|------|
| `/` | Dashboard (Command Center) |
| `/chat` | VIP Chatbot |
| `/agents` | Agent Registry |
| `/workflows` | Schedules & Workflows |
| `/reports` | Reports |
| `/judgement` | Judgement & Approvals |
| `/a2a` | A2A Monitor |
| `/channels` | Channels (Telegram, etc.) |
| `/ai-glass` | AI Glass Capture |

## Backend Requirements

Your backend must:
1. Be deployed and publicly accessible
2. Have CORS configured to allow Vercel domain
3. Be set as `NEXT_PUBLIC_API_BASE_URL`

## Pre-Deploy Checklist

Run before deploying:

```bash
cd apps/admin-dashboard
npm run check:deploy
```

This verifies:
- Environment variable configured
- Build succeeds
- All routes compile

## Risks & Notes

- **No SSR data fetching** — all API calls are client-side (`"use client"` pages)
- **CORS required** — backend must allow the Vercel domain
- **No secrets in frontend** — `NEXT_PUBLIC_` vars are visible in browser
- **API must be up** — dashboard shows empty states if backend is unreachable
