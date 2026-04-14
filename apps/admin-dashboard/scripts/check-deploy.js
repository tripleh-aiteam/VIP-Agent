#!/usr/bin/env node
/**
 * VIP Agent Dashboard — Deployment Readiness Check
 * Run: npm run check:deploy
 */

const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
let passed = 0;
let failed = 0;

function check(name, fn) {
  try {
    fn();
    console.log(`  ✅ ${name}`);
    passed++;
  } catch (e) {
    console.log(`  ❌ ${name}: ${e.message}`);
    failed++;
  }
}

console.log("\n🔍 VIP Dashboard — Deploy Readiness Check\n");

// 1. Check env var
check("NEXT_PUBLIC_API_BASE_URL configured", () => {
  const envLocal = path.join(ROOT, ".env.local");
  const envProd = path.join(ROOT, ".env.production");
  const hasLocal = fs.existsSync(envLocal) && fs.readFileSync(envLocal, "utf8").includes("NEXT_PUBLIC_API_BASE_URL");
  const hasProd = fs.existsSync(envProd) && fs.readFileSync(envProd, "utf8").includes("NEXT_PUBLIC_API_BASE_URL");
  const hasEnv = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!hasLocal && !hasProd && !hasEnv) throw new Error("Not set in .env.local, .env.production, or environment");
});

// 2. Check package.json scripts
check("package.json has build script", () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
  if (!pkg.scripts?.build) throw new Error("Missing 'build' script");
});

// 3. Check no hardcoded localhost in api.ts (except fallback)
check("No hardcoded localhost in components (except fallback)", () => {
  const apiFile = fs.readFileSync(path.join(ROOT, "src/components/api.ts"), "utf8");
  const lines = apiFile.split("\n");
  const hardcoded = lines.filter(l => l.includes("localhost:8000") && !l.includes("||") && !l.includes("//") && !l.includes("console"));
  if (hardcoded.length > 0) throw new Error(`Found hardcoded localhost: ${hardcoded[0].trim()}`);
});

// 4. Check critical page files exist
const pages = [
  "src/app/page.tsx",
  "src/app/chat/page.tsx",
  "src/app/agents/page.tsx",
  "src/app/workflows/page.tsx",
  "src/app/reports/page.tsx",
  "src/app/judgement/page.tsx",
  "src/app/a2a/page.tsx",
  "src/app/channels/page.tsx",
  "src/app/ai-glass/page.tsx",
];
check(`All ${pages.length} page files exist`, () => {
  const missing = pages.filter(p => !fs.existsSync(path.join(ROOT, p)));
  if (missing.length > 0) throw new Error(`Missing: ${missing.join(", ")}`);
});

// 5. Check next.config exists
check("next.config.js exists", () => {
  if (!fs.existsSync(path.join(ROOT, "next.config.js"))) throw new Error("Missing next.config.js");
});

// 6. Try build
check("Production build succeeds", () => {
  execSync("npm run build", { cwd: ROOT, stdio: "pipe", timeout: 120000 });
});

// Summary
console.log(`\n${passed + failed} checks: ${passed} passed, ${failed} failed\n`);
if (failed > 0) {
  console.log("❌ Not ready for deployment. Fix the issues above.\n");
  process.exit(1);
} else {
  console.log("✅ Ready for Vercel deployment!\n");
}
