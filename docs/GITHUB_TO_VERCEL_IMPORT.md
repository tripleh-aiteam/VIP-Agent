# VIP Agent Platform — GitHub to Vercel Import

## Step 1: Push to GitHub

```bash
cd vip-ai-platform
git add -A
git commit -m "Prepare for Vercel deployment"
git remote add origin https://github.com/YOUR_USER/vip-ai-platform.git
git push -u origin main
```

## Step 2: Create Vercel Project

1. Go to https://vercel.com/new
2. Click **"Add GitHub Account"** or select your account
3. Find and select **vip-ai-platform** repository
4. Click **Import**

## Step 3: Configure Project

| Setting | Value |
|---------|-------|
| **Project Name** | `vip-agent-dashboard` |
| **Framework** | Next.js (auto-detected) |
| **Root Directory** | Click **Edit** → type `apps/admin-dashboard` |
| **Build Command** | `npm run build` (default) |
| **Install Command** | `npm install` (default) |

## Step 4: Add Environment Variables

Click **Environment Variables** and add:

```
NEXT_PUBLIC_API_BASE_URL = https://your-backend-url.example.com
```

Select environments: **Production** and **Preview**.

## Step 5: Deploy

Click **Deploy**. Wait 1-2 minutes.

## Step 6: Verify

Open your Vercel URL and check:

- [ ] `/` loads with stats
- [ ] `/chat` opens chatbot
- [ ] `/agents` shows agent cards
- [ ] `/reports` shows report list
- [ ] `/judgement` shows cases
- [ ] No CORS errors in browser console

## If Something Fails

| Problem | Fix |
|---------|-----|
| "next: command not found" | Root Directory not set to `apps/admin-dashboard` |
| Pages load but empty | `NEXT_PUBLIC_API_BASE_URL` not set or wrong |
| CORS errors | Add Vercel domain to backend `CORS_ALLOWED_ORIGINS` |
| Build error | Run `cd apps/admin-dashboard && npm run check:deploy` locally |
