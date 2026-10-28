"use client";
import { useCallback, useEffect, useState } from "react";
import { ArrowUpRight, Check, Database, ExternalLink, FlaskConical, Info, Link2, Plus, Radio, ShieldCheck, Trash2 } from "lucide-react";
import { age, api } from "@/lib/api";
import type { Market, Sources as SourceData } from "@/lib/types";
import { useTerminal } from "./shell";
import { BookBadge, Empty, ErrorMessage, Field, Modal, Spinner } from "./ui";

const providers = [
  { id: "odds_api", label: "The Odds API", tag: "SPORTSBOOK ODDS", initials: "OA", color: "#b4e383", description: "FanDuel and DraftKings prices. Direct exchange connections are configured separately.", docs: "https://the-odds-api.com/liveapi/guides/v4/" },
  { id: "novig", label: "Novig direct", tag: "AUTHENTICATED LIVE STREAM", initials: "N", color: "#b5f256", description: "Direct order books with verified outcomes, penny payouts, and taker fees.", docs: "https://docs.novig.com" },
  { id: "kalshi", label: "Kalshi", tag: "DIRECT PUBLIC API", initials: "K", color: "#50d697", description: "Binary contracts with verified settlement mappings and rounded taker fees.", docs: "https://docs.kalshi.com" },
  { id: "polymarket_us", label: "Polymarket US", tag: "DIRECT PUBLIC API", initials: "P", color: "#7398f5", description: "US market books. Its contracts and eligibility are separate from international.", docs: "https://docs.polymarket.us" },
  { id: "polymarket", label: "Polymarket international", tag: "REFERENCE ONLY", initials: "P", color: "#9692eb", description: "International CLOB prices. Excluded from US execution opportunities.", docs: "https://docs.polymarket.com" }
];
const statusNames: Record<string, string> = { access_restricted: "Access restricted", paused: "Paused", no_data: "No matching odds", quota_exhausted: "Credits exhausted", authentication_failed: "Access rejected", rate_limited: "Rate limited", invalid_credentials: "Invalid credentials", disabled: "Not enabled", streaming: "Streaming", waiting_for_data: "Awaiting snapshot", reconnecting: "Reconnecting", credentials_required: "API credentials required", stream_only: "Stream setup required", connected: "Connected", demo: "Demo data", error: "Needs attention", missing_key: "API key required", needs_mapping: "Mapping required", budget_paused: "Budget paused" };
const credentialNames: Record<string, string> = { public: "No key needed", configured: "Credentials configured", missing: "Credentials missing", invalid: "Check credentials" };

export default function Sources() {
  const { data, notify } = useTerminal();
  const [sources, setSources] = useState<SourceData | null>(null), [error, setError] = useState(""), [mapping, setMapping] = useState(false), [retrying, setRetrying] = useState(false);
  const load = useCallback(async () => { try { setSources(await api<SourceData>("/sources")); setError(""); } catch (e) { setError((e as Error).message); } }, []);
  useEffect(() => { void load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [load]);
  return <><div className="page-heading"><div><div className="eyebrow"><span className="tiny-square" />THE INPUTS BEHIND YOUR EDGE</div><h1>Connected to the market<span className="heading-dot">.</span></h1><p>Market-data source health, coverage, and the contracts you have actually verified.</p></div><button className="button primary" onClick={() => setMapping(true)}><Plus size={15} />Map a market</button></div>
    {data.mode === "demo" && <div className="demo-banner"><div className="demo-icon"><FlaskConical size={16} /></div><div><strong>Demo mode is active.</strong><span>All displayed odds and history are synthetic. Live connections require server configuration.</span></div><a href="https://github.com/PlasmaZero/EVHunter#live-data" target="_blank" rel="noreferrer">Setup guide <ArrowUpRight size={14} /></a></div>}
    <div className="inline-note"><Info size={15} />These connections provide market prices. Bet history and account balances are tracked manually today. Read-only account sync is planned.</div>
    <ErrorMessage text={error} />
    <section className="panel settings-panel">
      <div className="panel-heading"><div><h3>Connection setup</h3><p>Credentials stay on your server. This page shows configuration status only.</p></div>
        <button className="button small" disabled={data.mode !== "live" || retrying} onClick={async () => {
          setRetrying(true);
          try { await api("/sources/retry", { method: "POST" }); notify("Sportsbook retry scheduled. Credit limits still apply."); await load(); }
          catch (e) { setError((e as Error).message); }
          finally { setRetrying(false); }
        }}>{retrying ? <Spinner /> : <Radio size={14} />}Retry sportsbook feed</button>
      </div>
      <div className="form-grid">
        <div><strong>{data.mode === "live" ? "Live mode active" : "Demo mode active"}</strong><p className="small-note">{data.mode === "demo" ? "Set DATA_MODE=live in the backend environment and restart to collect real odds. Adding a key alone does not switch workspaces." : "Real provider connections are enabled. Quotes still require fresh data and matching rules."}</p></div>
        <div><strong>Sportsbooks: {(sources?.configured_books || ["fanduel", "draftkings"]).map(b => data.books[b]?.name || b).join(", ")}</strong><p className="small-note">Monitored leagues: {sources?.enabled_sports?.join(", ") || "None"}. Choose leagues in Settings. Fanatics is excluded.</p></div>
      </div>
    </section>
    <div className="source-grid">{providers.map(p => {
      const restState = sources?.states[p.id], streamState = sources?.states[`${p.id}_stream`], setup = sources?.setup?.[p.id];
      const streamActive = streamState && ["streaming", "waiting_for_data", "reconnecting"].includes(streamState.status);
      const state = streamActive ? streamState : restState || streamState;
      const status = state?.status || "starting";
      return <section className="panel source-card" key={p.id}>
        <div className="source-heading"><span className="source-logo" style={{ color: p.color, borderColor: `${p.color}30` }}>{p.initials}</span><span className={`source-status ${status}`}><span className="status-dot" />{statusNames[status] || "Starting"}</span></div>
        <div className="source-tag">{p.tag}</div><h3>{p.label}</h3><p>{p.description}</p>
        {setup && <div className={`source-setup ${setup.status}`}>
          <strong>{credentialNames[setup.status]}</strong><p>{setup.message}</p>
          {p.id !== "odds_api" && <small>Stream {setup.enabled ? "enabled" : "disabled"} · {setup.mapping_count} reviewed {setup.mapping_count === 1 ? "mapping" : "mappings"}. A key does not match contracts automatically.</small>}
        </div>}
        <div className="source-measurements"><div><span>Refresh cadence</span><strong>{status === "streaming" ? "Push" : `${p.id === "odds_api" ? sources?.poll_seconds || 60 : sources?.prediction_poll_seconds || 20}s`}</strong></div><div><span>Last successful fetch</span><strong>{state?.last_success ? `${age(state.last_success)} ago` : "—"}</strong></div></div>
        <div className="source-message">{state?.message || "Waiting for collector status"}</div>
        {streamState && streamState !== state && streamState.status !== "disabled" && <div className="source-message">Stream: {streamState.message || statusNames[streamState.status]}</div>}
        <a className="source-docs" href={p.docs} target="_blank" rel="noreferrer">API documentation <ArrowUpRight size={14} /></a>
      </section>;
    })}</div>
    <div className="source-summary"><div><Radio size={16} /><span>Source refresh ≠ execution speed</span></div><p>Sportsbook feed updates are typically 40–60 seconds apart. Prices can move before your next refresh. Faster browser updates do not create fresher upstream data.</p></div>
    <section className="panel coverage-panel"><div className="panel-heading"><div><h3>Venue coverage</h3><p>Collected selections by venue. Counts include reference and excluded quotes.</p></div><span className="period-tag">{data.mode.toUpperCase()}</span></div><div className="coverage-grid">{Object.entries(data.books).filter(([book]) => data.preferences.allowed_books.includes(book) || book === data.preferences.reference_book || (sources?.quote_counts[book] || 0) > 0).map(([book, b]) => <div className="coverage-item" key={book}><BookBadge id={book} books={data.books} label /><span className="mono">{sources?.quote_counts[book] || 0}<small>quotes</small></span></div>)}</div><div className="coverage-footnote"><Info size={14} /><span>Live sportsbook rules require review. Novig and ProphetX fees require explicit configuration. Pinnacle’s aggregated reference may be delayed.</span></div></section>
    <section className="panel mappings-panel"><div className="panel-heading"><div><h3>Verified prediction-market mappings</h3><p>Exact outcomes, lines, overtime rules, and fees must match before comparison.</p></div><button className="button small" onClick={() => setMapping(true)}><Link2 size={14} />Add mapping</button></div>{sources?.mappings.length ? <div className="table-scroll"><table className="bet-table"><thead><tr><th>VENUE</th><th>EXTERNAL CONTRACT</th><th>YES OUTCOME</th><th>FEE MODEL</th><th /></tr></thead><tbody>{sources.mappings.map(m => <tr key={m.id}><td><BookBadge id={m.source} books={data.books} label /></td><td className="mono mapping-id">{m.external_id}</td><td>{m.yes_outcome}</td><td>{m.fee_model} · {m.fee_rate}</td><td><button className="icon-button" aria-label={`Remove mapping ${m.external_id}`} onClick={async () => { try { await api(`/mappings/${m.id}`, { method: "DELETE" }); await load(); notify("Mapping removed and linked quotes invalidated."); } catch (e) { setError((e as Error).message); } }}><Trash2 size={14} /></button></td></tr>)}</tbody></table></div> : <Empty title="Verified beats vaguely similar" detail="Map a specific Kalshi or Polymarket contract to an existing market. The collector will use that mapping in live mode." />}</section>
    <div className="source-budget"><Database size={17} /><div><strong>Collection budget</strong><p>{(sources?.monthly_credit_budget ?? 450).toLocaleString()} calendar-month credits · {sources?.states.odds_api?.budget_spent?.toLocaleString() || "0"} locally counted. Provider quotas and billing cycles are enforced separately. {sources?.states.odds_api?.credits_remaining != null && <>Provider credits remaining: {Number(sources.states.odds_api.credits_remaining).toLocaleString()}. </>}{data.collection && <>Continuous collection at the current settings is estimated at {data.collection.estimated_monthly_credits.toLocaleString()} credits/month; collection pauses when a limit is reached.</>}</p></div></div>
    {mapping && <MappingModal onClose={() => setMapping(false)} onSaved={load} />}
  </>;
}

function MappingModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const { data, notify } = useTerminal();
  const [markets, setMarkets] = useState<Market[]>([]), [market, setMarket] = useState("");
  const [source, setSource] = useState("kalshi"), [external, setExternal] = useState(""), [yes, setYes] = useState("");
  const [model, setModel] = useState("curve"), [rate, setRate] = useState("0.07"), [exponent, setExponent] = useState("1"), [tokens, setTokens] = useState(["", ""]);
  const [reviewed, setReviewed] = useState(false), [fees, setFees] = useState(false), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => { api<Market[]>("/markets").then(m => { setMarkets(m); if (m.length) { setMarket(m[0].id); setYes(m[0].outcomes[0]); } }).catch(e => setError(e.message)); }, []);
  const selected = markets.find(m => m.id === market);
  return <Modal title="Map a prediction market" subtitle="A reviewed mapping is required before live quotes enter comparisons." onClose={onClose}><form className="modal-body form-stack" onSubmit={async e => { e.preventDefault(); setBusy(true); setError(""); try { await api("/mappings", { method: "POST", body: JSON.stringify({ market_id: market, source, external_id: external, yes_outcome: yes, token_ids: ["polymarket", "novig"].includes(source) ? tokens : [], fee_model: model, fee_rate: Number(rate), fee_exponent: Number(exponent), rules_verified: reviewed, fees_reviewed: fees }) }); onSaved(); notify("Reviewed market mapping saved."); onClose(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }}>
    {data.mode === "demo" && <div className="inline-note"><Info size={15} />Demo mappings stay in this workspace. Create live mappings after connecting your sportsbook feed.</div>}
    <Field label="Canonical event and market"><select required value={market} onChange={e => { setMarket(e.target.value); setYes(markets.find(m => m.id === e.target.value)?.outcomes[0] || ""); }}><option value="" disabled>Select an ingested market</option>{markets.map(m => <option key={m.id} value={m.id}>{m.event} · {m.market}{m.line !== null ? ` ${m.line}` : ""}</option>)}</select></Field>
    <Field label="Prediction venue"><select value={source} onChange={e => { setSource(e.target.value); setModel(e.target.value === "kalshi" ? "curve" : e.target.value === "novig" ? "none" : "per_contract"); setRate(e.target.value === "kalshi" ? "0.07" : "0"); }}><option value="kalshi">Kalshi</option><option value="novig">Novig · direct stream</option><option value="polymarket_us">Polymarket US</option><option value="polymarket">Polymarket international · reference only</option></select></Field>
    <Field label={source === "kalshi" ? "Market ticker" : source === "polymarket_us" ? "Market slug" : source === "novig" ? "Novig market ID" : "Condition ID"}><input required value={external} maxLength={200} onChange={e => setExternal(e.target.value)} placeholder={source === "kalshi" ? "KX..." : source === "polymarket_us" ? "market-slug" : "0x..."} /></Field>
    <Field label="YES corresponds to"><select required value={yes} onChange={e => setYes(e.target.value)}>{selected?.outcomes.map(o => <option key={o}>{o}</option>)}</select></Field>
    {["polymarket", "novig"].includes(source) && <div className="form-grid">{tokens.map((t, i) => <Field key={i} label={`${i === 0 ? "YES" : "NO"} outcome ${source === "novig" ? "ID" : "token ID"}`}><input required value={t} onChange={e => setTokens(a => a.map((v, j) => i === j ? e.target.value : v))} /></Field>)}</div>}
    <div className="form-grid"><Field label="Reviewed taker fee model"><select value={model} onChange={e => { setModel(e.target.value); if (e.target.value === "none") setRate("0"); }}><option value="curve">Curve: rate × shares × [p(1−p)]^exponent</option><option value="per_contract">Flat fee per contract ($)</option><option value="none">Verified zero fee</option></select></Field><Field label="Rate / coefficient"><input required type="number" min="0" max="1" step="any" value={rate} onChange={e => setRate(e.target.value)} /></Field></div>{model === "curve" && <Field label="Curve exponent"><input required type="number" min="1" max="5" step="any" value={exponent} onChange={e => setExponent(e.target.value)} /></Field>}
    <div className="inline-note"><Info size={15} />Novig contracts pay $0.01; other supported contracts pay $1. Curve fees are scaled to the contract payout. Review whether the fee applies pregame.</div><label className="check-line"><input type="checkbox" required checked={reviewed} onChange={e => setReviewed(e.target.checked)} />I checked the exact line, outcomes, overtime, and cancellation rules.</label><label className="check-line"><input type="checkbox" required checked={fees} onChange={e => setFees(e.target.checked)} />I verified this contract’s current fee schedule.</label><ErrorMessage text={error} /><div className="modal-actions"><button className="button" type="button" onClick={onClose}>Cancel</button><button className="button primary" disabled={busy || !market}>{busy ? <Spinner /> : <Check size={15} />}Save mapping</button></div>
  </form></Modal>;
}
