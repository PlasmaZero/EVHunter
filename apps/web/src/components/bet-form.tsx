"use client";
import { useState } from "react";
import { Check, Info } from "lucide-react";
import { api, cash } from "@/lib/api";
import type { Opportunity } from "@/lib/types";
import { useTerminal } from "./shell";
import { ErrorMessage, Field, Modal, Spinner } from "./ui";

export default function BetForm({ opportunity: op, onClose, onSaved }: { opportunity?: Opportunity; onClose: () => void; onSaved?: () => void }) {
  const { data, refresh, notify } = useTerminal();
  const [leg, setLeg] = useState(0);
  const [event, setEvent] = useState(op?.event || "");
  const [selection, setSelection] = useState(op?.type === "arb" ? op.legs[0].selection : op?.selection || "");
  const [book, setBook] = useState(op?.books[0] || "fanduel");
  const [stake, setStake] = useState(op?.type === "arb" ? op.legs[0].stake.toFixed(2) : op?.stake.toFixed(2) || "100");
  const [odds, setOdds] = useState(op?.type === "arb" ? op.legs[0].odds.toFixed(4) : op?.odds?.toFixed(4) || "1.91");
  const [probability, setProbability] = useState(op?.true_probability ? (op.true_probability*100).toFixed(3) : "");
  const [entryFee, setEntryFee] = useState("0"), [winFee, setWinFee] = useState("0"), [notes, setNotes] = useState("");
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      await api("/bets", { method: "POST", body: JSON.stringify({ event, selection, book, market: op?.market || "manual", stake: Number(stake), odds: Number(odds), entry_fee: Number(entryFee), win_fee: Number(winFee), probability: probability ? Number(probability)/100 : null, snapshot_id: op?.snapshot_id || null, notes }) });
      await refresh(); onSaved?.(); notify("Bet recorded. Tracked exposure updated."); onClose();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <Modal title="Log a bet" subtitle="Record a bet you already placed at a sportsbook or exchange." onClose={onClose}>
    <form onSubmit={submit} className="modal-body form-stack">
      <div className="inline-note"><Info size={15} />Manual tracking works without setting a bankroll. Enter the stake and odds confirmed by your venue.</div>
      {data.mode === "demo" && <div className="inline-note"><Info size={15} />This entry belongs to your separate demo bankroll.</div>}
      {op?.type === "arb" && <Field label="Arbitrage leg" hint="Log each accepted leg separately. A single leg does not lock in the arbitrage."><select value={leg} onChange={e => { const i = Number(e.target.value), l = op.legs[i]; setLeg(i); setSelection(l.selection); setBook(l.book); setStake(l.stake.toFixed(2)); setOdds(l.odds.toFixed(4)); }}>{op.legs.map((l, i) => <option key={i} value={i}>{l.selection} · {data.books[l.book].name}</option>)}</select></Field>}
      <Field label="Event"><input required readOnly={!!op} maxLength={180} value={event} onChange={e => setEvent(e.target.value)} placeholder="Boston Celtics @ New York Knicks" /></Field>
      <Field label="Selection"><input required readOnly={!!op} maxLength={180} value={selection} onChange={e => setSelection(e.target.value)} placeholder="Over 224.5" /></Field>
      <Field label="Venue"><select value={book} onChange={e => setBook(e.target.value)}>{Object.entries(data.books).filter(([k]) => k !== "pinnacle" && k !== "polymarket").map(([k, b]) => <option key={k} value={k}>{b.name}</option>)}</select></Field>
      <div className="form-grid"><Field label="Wager stake ($)"><input required type="number" min="0.01" step="0.01" value={stake} onChange={e => setStake(e.target.value)} /></Field><Field label="Accepted decimal odds"><input required type="number" min="1.0001" step="0.0001" value={odds} onChange={e => setOdds(e.target.value)} /></Field></div>
      <Field label="Estimated true probability (%)" hint={op?.true_probability ? "Reference probability is preserved from the linked server snapshot." : "Optional. Leave blank when no probability estimate is available."}><input type="number" min="0.001" max="99.999" step="0.001" value={probability} readOnly={!!op?.true_probability} onChange={e => setProbability(e.target.value)} placeholder="e.g. 54.5" /></Field>
      <details className="fee-details"><summary>Fees and notes</summary><div className="form-grid"><Field label="Entry fee ($)"><input type="number" min="0" step="0.01" value={entryFee} onChange={e => setEntryFee(e.target.value)} /></Field><Field label="Fee if this bet wins ($)"><input type="number" min="0" step="0.01" value={winFee} onChange={e => setWinFee(e.target.value)} /></Field></div><Field label="Notes"><textarea maxLength={1000} rows={2} value={notes} onChange={e => setNotes(e.target.value)} placeholder="Optional notes about the accepted bet" /></Field></details>
      <div className="form-total"><span>Total cash risked</span><strong>{cash((Number(stake)||0)+(Number(entryFee)||0))}</strong></div>
      <ErrorMessage text={error} /><div className="modal-actions"><button type="button" className="button" onClick={onClose}>Cancel</button><button className="button primary" disabled={busy}>{busy ? <Spinner /> : <Check size={15} />}Record bet</button></div>
    </form>
  </Modal>;
}
