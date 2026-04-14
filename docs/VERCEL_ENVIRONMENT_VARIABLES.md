# VIP Agent Platform — Vercel Environment Variables

## Frontend Variables (Safe for Browser)

These use the `NEXT_PUBLIC_` prefix — they are embedded in the JavaScript bundle and **visible to users** in browser dev tools. Only put non-secret values here.

| Variable | Purpose | Required | Preview Value | Production Value |
|----------|---------|----------|---------------|------------------|
| `NEXT_PUBLIC_API_BASE_URL` | Backend API URL | Yes | `https://staging-api.example.com` | `https://api.vip-agent.example.com` |
| `NEXT_PUBLIC_APP_ENV` | Environment label | No | `preview` | `production` |
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL (if direct access needed) | No | `https://xxx.supabase.co` | `https://xxx.supabase.co` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon key (public, read-only) | No | `eyJ...` | `eyJ...` |

### Setup in Vercel

1. Go to **Project Settings > Environment Variables**
2. Add each variable
3. Select environments: **Production**, **Preview**, or both
4. Click **Save**

### Per-Environment Values

Vercel lets you set different values per environment:

| Environment | `NEXT_PUBLIC_API_BASE_URL` | `NEXT_PUBLIC_APP_ENV` |
|-------------|---------------------------|----------------------|
| Production | `https://api.vip-agent.example.com` | `production` |
| Preview | `https://staging-api.example.com` | `preview` |
| Development | `http://localhost:8000` | `development` |

## Backend-Only Variables (NEVER Put in Frontend)

These contain secrets and must **only** exist on your backend server. Never add these to Vercel frontend project.

| Variable | Where It Belongs | Why It's Secret |
|----------|-----------------|-----------------|
| `DATABASE_URL` | Backend server | Full database access |
| `OPENAI_API_KEY` | Backend server | API billing |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend server | Bypasses row-level security |
| `TELEGRAM_BOT_TOKEN` | Backend server | Bot control |
| `REDIS_URL` | Backend server | Internal infrastructure |
| `CORS_ALLOWED_ORIGINS` | Backend server | Backend config only |

### Why This Matters

`NEXT_PUBLIC_` variables are compiled into the frontend JavaScript at build time. Anyone can see them by opening browser dev tools. Putting a database URL or API key here would expose it to the public internet.

**Rule:** If it's a password, key, or connection string — it goes on the backend server, not in Vercel frontend env vars.

## Supabase Keys — Which Is Which?

| Key | Prefix | Safe for Frontend? | Purpose |
|-----|--------|-------------------|---------|
| `anon` key | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Yes | Public read access with RLS |
| `service_role` key | Never in frontend | **No** | Admin access, bypasses RLS |

**Currently:** The VIP frontend does not call Supabase directly — all data goes through the backend API. The Supabase env vars are optional and only needed if you add direct Supabase features to the frontend later.

## Quick Copy-Paste for Vercel

### Production
```
NEXT_PUBLIC_API_BASE_URL = https://api.vip-agent.example.com
NEXT_PUBLIC_APP_ENV = production
```

### Preview
```
NEXT_PUBLIC_API_BASE_URL = https://staging-api.example.com
NEXT_PUBLIC_APP_ENV = preview
```
