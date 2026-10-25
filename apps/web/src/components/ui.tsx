"use client";
import { useEffect, useRef } from "react";
import { X, ArrowUpRight, Info, LoaderCircle } from "lucide-react";
import type { Books } from "@/lib/types";

export function Logo({ small = false }: { small?: boolean }) {
  return <div className={`logo ${small ? "small" : ""}`}><div className="logo-symbol"><svg viewBox="0 0 26 26" fill="none"><path d="M5 19V8h5M5 13h7M12 19l4-11h5l-4 11h-5Z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" /></svg></div>{!small && <span>EV<span className="logo-light">Hunter</span><span className="logo-dot">.</span></span>}</div>;
}
export function BookBadge({ id, books, label = false }: { id: string; books: Books; label?: boolean }) {
  const b = books[id] || { short: id.slice(0, 2), name: id, color: "#8ca09b" };
  return <span className="book-wrapper" title={b.name}><span className="book-badge" style={{ color: b.color, borderColor: `${b.color}38`, background: `${b.color}0d` }}>{b.short}</span>{label && <span>{b.name}</span>}</span>;
}
export function ErrorMessage({ text }: { text: string }) { return text ? <div className="error-message" role="alert"><Info size={15} />{text}</div> : null; }
export function Spinner() { return <LoaderCircle className="spin" size={18} />; }
export function Empty({ title, detail }: { title: string; detail: string }) { return <div className="empty"><div className="empty-icon"><ArrowUpRight size={24} /></div><h3>{title}</h3><p>{detail}</p></div>; }
export function Modal({ title, subtitle, children, onClose, wide = false }: { title: string; subtitle?: string; children: React.ReactNode; onClose: () => void; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; }, [onClose]);
  useEffect(() => {
    const prior = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const old = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    function key(e: KeyboardEvent) {
      if (e.key === "Escape") closeRef.current();
      if (e.key === "Tab") {
        const nodes = ref.current?.querySelectorAll<HTMLElement>('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea,a[href]');
        if (!nodes?.length) return;
        const first = nodes[0], last = nodes[nodes.length-1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }
    document.addEventListener("keydown", key);
    return () => { document.body.style.overflow = old; document.removeEventListener("keydown", key); prior?.focus(); };
  }, []);
  return <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}><div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title} className={`modal ${wide ? "modal-wide" : ""}`}><div className="modal-heading"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={19} /></button></div>{children}</div></div>;
}
export function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) { return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>; }
