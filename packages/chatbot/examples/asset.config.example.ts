/**
 * EXAMPLE — Asset Agent's chatbot configuration.
 *
 * This file shows how an entirely DIFFERENT agent (real estate management)
 * uses the SAME @triple-h/chatbot module by providing a completely
 * different config. The chatbot module itself is unchanged — only the
 * config below differs.
 *
 * Compare with vip.config.ts in admin-dashboard: same shape, totally
 * different content. The module doesn't know or care what agent it
 * serves — it just renders whatever the config provides.
 */

import type { AgentConfig } from "../src/types";

export const assetConfig: AgentConfig = {
  agentId: "asset",
  apiBase: process.env.NEXT_PUBLIC_ASSET_API || "https://asset-agent-s4tw.onrender.com",

  identity: {
    name: "Asset Manager",
    greeting: {
      en: "Hi! I'm your real estate assistant. Ask about properties, leases, or rent collection.",
      ko: "안녕하세요. 부동산 관리 비서입니다. 건물, 임대, 임대료에 대해 물어보세요.",
    },
    wakeWords: {
      en: ["hey asset", "asset manager", "hey property"],
      ko: ["부동산", "자산 관리"],
    },
    tone: "formal",
  },

  // Asset-specific intents — totally different from VIP's
  intents: [
    { name: "list_properties", description: "List all properties in the portfolio",
      examples: { en: ["show me my properties", "list buildings", "what do I own"],
                  ko: ["내 건물 목록", "부동산 보여줘"] },
      action: { type: "data_query", endpoint: "/api/property/list" } },
    { name: "monthly_income", description: "Total monthly rental income",
      examples: { en: ["how much rent this month", "monthly income", "what's my cashflow"],
                  ko: ["이번 달 임대료", "월 수입"] },
      action: { type: "data_query", endpoint: "/api/cash/rental-income/summary" } },
    { name: "occupancy", description: "Current vacancy/occupancy rates",
      examples: { en: ["what's my occupancy", "any vacant units"],
                  ko: ["공실률", "공실 있어"] },
      action: { type: "data_query", endpoint: "/api/lease/vacancies" } },
    { name: "expiring_leases", description: "Leases expiring soon",
      examples: { en: ["what leases are expiring", "lease expiry"],
                  ko: ["만료 임박 계약"] },
      action: { type: "data_query", endpoint: "/api/lease/expiries" } },
    { name: "overdue", description: "Tenants with overdue rent",
      examples: { en: ["who's behind on rent", "overdue tenants"],
                  ko: ["미수금"] },
      action: { type: "data_query", endpoint: "/api/lease/receivables" } },
    { name: "nav_dashboard", description: "Open dashboard",
      examples: { en: ["dashboard", "home"], ko: ["대시보드", "홈"] },
      action: { type: "navigate", to: "/" } },
    { name: "nav_properties", description: "Open the properties listing page",
      examples: { en: ["open properties", "go to properties"], ko: ["부동산 페이지"] },
      action: { type: "navigate", to: "/properties" } },
  ],

  knowledge: [
    { name: "portfolio_summary", description: "Total properties, value, monthly income",
      endpoint: "/api/dashboard/summary" },
  ],

  // Asset-specific knowledge base — entirely different from VIP's
  knowledgeBase: {
    purpose:
      "Asset Manager is a real estate operations platform for managing your property portfolio. " +
      "Track all your buildings, units, tenants, leases, rent collection, cash forecasts, " +
      "tax obligations, and loan repayments in one place. Get alerted when leases are about to " +
      "expire, when rent is overdue, or when cash flow projections look concerning.",
    menus: [
      { name: "Dashboard",   path: "/",            description: "Overview — total properties, monthly income, occupancy rate, upcoming lease expiries, overdue payments at a glance." },
      { name: "Properties",  path: "/properties",  description: "All buildings you own. Click each to see units, valuations, public-data lookups, tax liability." },
      { name: "Tenants",     path: "/tenants",     description: "Every tenant in your portfolio — contact info, payment history, credit grade, lease status." },
      { name: "Leases",      path: "/leases",      description: "Active and expired lease contracts. Renewal dates, monthly rent, deposit amounts, arrears tier." },
      { name: "Cash",        path: "/cash",        description: "Cash positions, rental income summary, monthly forecasts. Bank account balances roll up here." },
      { name: "Tax",         path: "/tax",         description: "Property tax records, comprehensive real estate tax, rental income tax, capital gains calculator, filing calendar." },
      { name: "Approvals",   path: "/approvals",   description: "Decisions awaiting your review — major repairs, new tenant applications, lease modifications." },
      { name: "Settings",    path: "/settings",    description: "API keys, Telegram bot config, user preferences." },
    ],
    features: [
      { name: "Excel Bulk Import", description: "Upload an Excel sheet with properties, units, tenants, and leases — they get created in bulk.",
        how_to: "Settings → Upload → drag-drop your Excel file. Template available at /api/upload/template." },
      { name: "Public Data Sync", description: "Automatically pulls government land prices, building info, and recent transaction data for each property.",
        how_to: "Click any property → 'Sync Public Data'." },
      { name: "Tax Calendar", description: "Korea-specific tax filing reminders — property tax (June + September), comprehensive tax (December), VAT (quarterly).",
        how_to: "Tax → Calendar shows upcoming due dates." },
      { name: "Tenant Notifications", description: "Send SMS, certified mail, or trigger phone calls to tenants for late rent, lease renewals, or notices.",
        how_to: "Tenants → click tenant → 'Send Notification'." },
    ],
    faq: [
      { q: "How do I add a new property?",
        a: "Click '+' on the Properties page or use the Excel bulk import for multiple at once." },
      { q: "Can I track loans?",
        a: "Yes — each property can have linked loans with collateral amount, interest rate, maturity date." },
      { q: "How is the yield calculated?",
        a: "Annual rental income (12 × monthly rent across all units) ÷ acquisition price × 100." },
    ],
  },

  // Asset-specific theme — green/professional, smaller panel
  theme: {
    primaryColor: "#059669",   // emerald
    accentColor:  "#0EA5E9",   // sky
    radius:       "md",
    panelWidth:   420,         // smaller than VIP
    panelHeight:  580,
    position:     "bottom-right",
  },

  supportedLanguages: ["auto", "en", "ko"],
};
