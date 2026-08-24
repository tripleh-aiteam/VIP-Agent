// 🎯 체크리스트 추천 데스크 — the reco desk's OWN route (boss 2026-08-24: query-param
// navigation between the two desks did not remount the shared page, so headers and
// stock lists mixed). The page component detects this pathname and renders the
// reco view: score-ranked stocks only, GO/NO-GO board, reco title.
export { default } from "../live/page";
