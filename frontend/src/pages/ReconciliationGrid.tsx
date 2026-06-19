import { useState, useEffect, useRef, CSSProperties } from "react";
import type { MatchResult, MatchStatus } from "../types";

const STATUS_COLOR: Record<MatchStatus, string> = {
  MATCHED:    "#22c55e",
  PROBABLE:   "#f59e0b",
  MISMATCH:   "#ef4444",
  BOOKS_ONLY: "#6366f1",
  TWOB_ONLY:  "#8b5cf6",
  CORRECTED:  "#0ea5e9",
};

const STATUS_LABEL: Record<MatchStatus, string> = {
  MATCHED:    "Matched",
  PROBABLE:   "Probable",
  MISMATCH:   "Mismatch",
  BOOKS_ONLY: "Books Only",
  TWOB_ONLY:  "2B Only",
  CORRECTED:  "Corrected",
};

const ALL_STATUSES: MatchStatus[] = [
  "MATCHED", "PROBABLE", "MISMATCH", "BOOKS_ONLY", "TWOB_ONLY", "CORRECTED",
];

const CORRECTION_REASONS = ["DataEntry", "Vendor", "Rounding"] as const;
type CorrectionReason = typeof CORRECTION_REASONS[number];

interface CorrectionForm {
  corrected_amount: string;
  corrected_date: string;
  reason: CorrectionReason;
  notes: string;
}

interface Toast {
  id: number;
  message: string;
  kind: "success" | "error";
}

interface Props {
  orgId: string;
  clientId: string;
  period: string;
}

const PAGE_SIZE = 50;

export default function ReconciliationGrid({ orgId, clientId, period }: Props) {
  const [results, setResults]           = useState<MatchResult[]>([]);
  const [totalCount, setTotalCount]     = useState(0);
  const [page, setPage]                 = useState(1);
  const [loading, setLoading]           = useState(false);
  const [filter, setFilter]             = useState<MatchStatus | "ALL">("ALL");
  const [error, setError]               = useState<string | null>(null);
  const [correctingId, setCorrectingId] = useState<string | null>(null);
  const [toasts, setToasts]             = useState<Toast[]>([]);
  const toastIdRef                      = useRef(0);

  useEffect(() => {
    if (!orgId || !clientId || !period) return;
    setLoading(true);
    setError(null);
    fetch(
      `/api/v1/reconciliation?client_id=${clientId}&period=${period}&page=${page}&page_size=${PAGE_SIZE}`,
      { headers: { "x-org-id": orgId } },
    )
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<{ results: MatchResult[]; total_count: number; page: number; page_size: number; total_pages: number }>;
      })
      .then((data) => {
        setResults(data.results);
        setTotalCount(data.total_count);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [orgId, clientId, period, page]);

  function pushToast(message: string, kind: Toast["kind"]) {
    const id = ++toastIdRef.current;
    setToasts((prev) => [...prev, { id, message, kind }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4000);
  }

  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));

  function setFilterAndReset(f: MatchStatus | "ALL") {
    setFilter(f);
    setPage(1);
  }

  const displayed =
    filter === "ALL" ? results : results.filter((r) => r.status === filter);

  const counts = Object.fromEntries(
    ALL_STATUSES.map((s) => [s, results.filter((r) => r.status === s).length])
  ) as Record<MatchStatus, number>;

  async function handleConfirm(id: string) {
    await fetch(`/api/v1/reconciliation/${id}/confirm`, {
      method: "PATCH",
      headers: { "x-org-id": orgId },
    });
    setResults((prev) =>
      prev.map((r) =>
        r.id === id ? { ...r, reviewed_at: new Date().toISOString() } : r
      )
    );
  }

  async function handleCorrectSubmit(id: string, form: CorrectionForm) {
    try {
      const res = await fetch(`/api/v1/reconciliation/${id}/correct`, {
        method: "POST",
        headers: { "x-org-id": orgId, "Content-Type": "application/json" },
        body: JSON.stringify({
          corrected_amount: form.corrected_amount ? Number(form.corrected_amount) : null,
          corrected_date:   form.corrected_date   || null,
          reason:           form.reason,
          notes:            form.notes            || null,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error((data as { detail?: string }).detail ?? `HTTP ${res.status}`);
      }
      setResults((prev) =>
        prev.map((r) => (r.id === id ? { ...r, ...(data as Partial<MatchResult>) } : r))
      );
      setCorrectingId(null);
      pushToast("Correction submitted successfully.", "success");
    } catch (err) {
      pushToast(`Correction failed: ${(err as Error).message}`, "error");
    }
  }

  return (
    <div style={styles.container}>
      {/* Filter bar */}
      <div style={styles.filterBar}>
        <FilterChip
          label="All"
          count={totalCount}
          active={filter === "ALL"}
          onClick={() => setFilterAndReset("ALL")}
        />
        {ALL_STATUSES.map((s) => (
          <FilterChip
            key={s}
            label={STATUS_LABEL[s]}
            count={counts[s]}
            color={STATUS_COLOR[s]}
            active={filter === s}
            onClick={() => setFilterAndReset(s)}
          />
        ))}
      </div>

      {error   && <p style={styles.error}>Error loading results: {error}</p>}
      {loading && <p style={styles.hint}>Loading…</p>}

      {!loading && !error && (
        <div style={styles.tableWrap}>
          <table style={styles.table}>
            <thead>
              <tr style={styles.headerRow}>
                {[
                  "Supplier GSTIN", "Supplier Name", "Invoice No", "Date",
                  "Taxable (₹)", "CGST (₹)", "SGST (₹)", "IGST (₹)",
                  "Status", "Confidence", "Actions",
                ].map((h) => (
                  <th key={h} style={styles.th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {displayed.length === 0 ? (
                <tr>
                  <td colSpan={11} style={styles.empty}>
                    {results.length === 0
                      ? "No results yet. Upload a purchase register and GSTR-2B to start reconciliation."
                      : "No entries match the selected filter."}
                  </td>
                </tr>
              ) : (
                displayed.map((row) => (
                  <ResultRow
                    key={row.id}
                    row={row}
                    onConfirm={handleConfirm}
                    onCorrect={(id) => setCorrectingId(id)}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {!loading && !error && totalCount > 0 && (
        <div style={styles.pagination}>
          <button
            style={{ ...styles.pageBtn, opacity: page <= 1 ? 0.4 : 1 }}
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            ← Prev
          </button>
          <span style={styles.pageInfo}>
            Showing {Math.min((page - 1) * PAGE_SIZE + 1, totalCount)}–{Math.min(page * PAGE_SIZE, totalCount)} of {totalCount}
          </span>
          <button
            style={{ ...styles.pageBtn, opacity: page >= totalPages ? 0.4 : 1 }}
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next →
          </button>
        </div>
      )}

      {/* Correction modal */}
      {correctingId && (
        <CorrectionModal
          resultId={correctingId}
          onSubmit={handleCorrectSubmit}
          onClose={() => setCorrectingId(null)}
        />
      )}

      {/* Toast stack */}
      <ToastStack toasts={toasts} />
    </div>
  );
}

// ── CorrectionModal ───────────────────────────────────────────────────────────

function CorrectionModal({
  resultId,
  onSubmit,
  onClose,
}: {
  resultId: string;
  onSubmit: (id: string, form: CorrectionForm) => Promise<void>;
  onClose: () => void;
}) {
  const [form, setForm] = useState<CorrectionForm>({
    corrected_amount: "",
    corrected_date:   "",
    reason:           "DataEntry",
    notes:            "",
  });
  const [submitting, setSubmitting] = useState(false);

  function set<K extends keyof CorrectionForm>(key: K, value: CorrectionForm[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await onSubmit(resultId, form);
    } finally {
      setSubmitting(false);
    }
  }

  // Close on backdrop click
  function handleBackdrop(e: React.MouseEvent<HTMLDivElement>) {
    if (e.target === e.currentTarget) onClose();
  }

  return (
    <div style={modal.backdrop} onClick={handleBackdrop}>
      <div style={modal.box} role="dialog" aria-modal="true" aria-label="Submit correction">
        <div style={modal.header}>
          <span style={modal.title}>Submit Correction</span>
          <button style={modal.closeBtn} onClick={onClose} aria-label="Close">✕</button>
        </div>

        <form onSubmit={handleSubmit} style={modal.form}>
          {/* Corrected amount */}
          <div style={modal.field}>
            <label style={modal.label}>Corrected Amount (₹)</label>
            <input
              type="number"
              step="0.01"
              min="0"
              placeholder="Leave blank to keep original"
              style={modal.input}
              value={form.corrected_amount}
              onChange={(e) => set("corrected_amount", e.target.value)}
            />
          </div>

          {/* Corrected date */}
          <div style={modal.field}>
            <label style={modal.label}>Corrected Date</label>
            <input
              type="date"
              style={modal.input}
              value={form.corrected_date}
              onChange={(e) => set("corrected_date", e.target.value)}
            />
          </div>

          {/* Reason */}
          <div style={modal.field}>
            <label style={modal.label}>Reason <span style={modal.required}>*</span></label>
            <select
              required
              style={modal.input}
              value={form.reason}
              onChange={(e) => set("reason", e.target.value as CorrectionReason)}
            >
              {CORRECTION_REASONS.map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </div>

          {/* Notes */}
          <div style={modal.field}>
            <label style={modal.label}>Notes</label>
            <textarea
              rows={3}
              placeholder="Optional details…"
              style={{ ...modal.input, resize: "vertical", height: "auto", padding: "8px 10px" }}
              value={form.notes}
              onChange={(e) => set("notes", e.target.value)}
            />
          </div>

          <div style={modal.actions}>
            <button
              type="button"
              style={modal.cancelBtn}
              onClick={onClose}
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              type="submit"
              style={{ ...modal.submitBtn, opacity: submitting ? 0.6 : 1 }}
              disabled={submitting}
            >
              {submitting ? "Submitting…" : "Submit Correction"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── ToastStack ────────────────────────────────────────────────────────────────

function ToastStack({ toasts }: { toasts: Toast[] }) {
  if (toasts.length === 0) return null;
  return (
    <div style={toastStyles.stack}>
      {toasts.map((t) => (
        <div
          key={t.id}
          style={{
            ...toastStyles.toast,
            background: t.kind === "success" ? "#15803d" : "#dc2626",
          }}
        >
          {t.message}
        </div>
      ))}
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function FilterChip({
  label, count, color, active, onClick,
}: {
  label: string;
  count: number;
  color?: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "6px 14px",
        borderRadius: 20,
        border: `1px solid ${active ? (color ?? "#1d4ed8") : "#d1d5db"}`,
        background: active ? (color ? color + "20" : "#eff6ff") : "#fff",
        color: active ? (color ?? "#1d4ed8") : "#6b7280",
        cursor: "pointer",
        fontWeight: active ? 600 : 400,
        fontSize: 13,
      }}
    >
      {label}
      <span
        style={{
          background: active ? (color ?? "#1d4ed8") : "#e5e7eb",
          color: active ? "#fff" : "#374151",
          borderRadius: 10,
          padding: "1px 7px",
          fontSize: 11,
          fontWeight: 600,
        }}
      >
        {count}
      </span>
    </button>
  );
}

function ResultRow({
  row, onConfirm, onCorrect,
}: {
  row: MatchResult;
  onConfirm: (id: string) => void;
  onCorrect: (id: string) => void;
}) {
  const entry = row.pr_entry ?? row.gstr2b_entry;
  const mismatch = new Set(row.mismatched_fields ?? []);

  const cell = (field: string, value: string | null | undefined) => (
    <td
      style={{
        ...styles.td,
        background: mismatch.has(field) ? "#fef2f2" : undefined,
        color:      mismatch.has(field) ? "#b91c1c" : undefined,
      }}
    >
      {value ?? "—"}
    </td>
  );

  return (
    <tr style={styles.row}>
      {cell("supplier_gstin", entry?.supplier_gstin)}
      {cell("supplier_name",  entry?.supplier_name)}
      {cell("inv_no",         entry?.inv_no)}
      {cell("inv_date",       entry?.inv_date)}
      {cell("taxable_value",  entry?.taxable_value)}
      {cell("cgst",           entry?.cgst)}
      {cell("sgst",           entry?.sgst)}
      {cell("igst",           entry?.igst)}
      <td style={styles.td}>
        <StatusBadge status={row.status} />
      </td>
      <td style={{ ...styles.td, textAlign: "right" }}>
        {row.confidence != null
          ? `${(row.confidence * 100).toFixed(0)}%`
          : "—"}
      </td>
      <td style={styles.td}>
        {row.reviewed_at ? (
          <span style={styles.reviewed}>Reviewed</span>
        ) : (row.status === "PROBABLE" || row.status === "MISMATCH") ? (
          <div style={{ display: "flex", gap: 6 }}>
            <button style={styles.confirmBtn} onClick={() => onConfirm(row.id)}>
              Confirm
            </button>
            <button style={styles.correctBtn} onClick={() => onCorrect(row.id)}>
              Correct
            </button>
          </div>
        ) : null}
      </td>
    </tr>
  );
}

function StatusBadge({ status }: { status: MatchStatus }) {
  const color = STATUS_COLOR[status];
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 10px",
        borderRadius: 12,
        background: color + "20",
        color,
        fontWeight: 600,
        fontSize: 12,
        whiteSpace: "nowrap",
      }}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const styles: Record<string, CSSProperties> = {
  container:  { fontFamily: "system-ui, sans-serif", padding: 24 },
  filterBar:  { display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 20 },
  tableWrap:  { overflowX: "auto" },
  table:      { width: "100%", borderCollapse: "collapse", fontSize: 13 },
  headerRow:  { background: "#f9fafb" },
  th: {
    padding: "10px 14px",
    fontWeight: 600,
    color: "#374151",
    borderBottom: "2px solid #e5e7eb",
    textAlign: "left",
    whiteSpace: "nowrap",
  },
  td: {
    padding: "10px 14px",
    color: "#1f2937",
    borderBottom: "1px solid #f3f4f6",
  },
  row:        { transition: "background 0.1s" },
  empty:      { textAlign: "center", padding: 48, color: "#9ca3af" },
  hint:       { color: "#6b7280", padding: "8px 0" },
  error:      { color: "#dc2626", padding: "8px 0" },
  reviewed:   { color: "#6b7280", fontSize: 12 },
  pagination: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 16,
    padding: "16px 0 4px",
  },
  pageBtn: {
    padding: "6px 14px",
    borderRadius: 6,
    border: "1px solid #d1d5db",
    background: "#fff",
    color: "#374151",
    fontSize: 13,
    fontWeight: 500,
    cursor: "pointer",
  },
  pageInfo: {
    fontSize: 13,
    color: "#374151",
    minWidth: 140,
    textAlign: "center" as const,
  },
  pageTotal: {
    color: "#9ca3af",
  },
  confirmBtn: {
    padding: "4px 10px", borderRadius: 4, fontSize: 12, cursor: "pointer",
    border: "1px solid #22c55e", background: "#f0fdf4", color: "#15803d",
  },
  correctBtn: {
    padding: "4px 10px", borderRadius: 4, fontSize: 12, cursor: "pointer",
    border: "1px solid #f59e0b", background: "#fffbeb", color: "#b45309",
  },
};

const modal: Record<string, CSSProperties> = {
  backdrop: {
    position: "fixed",
    inset: 0,
    background: "rgba(15,23,42,0.45)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  },
  box: {
    background: "#fff",
    borderRadius: 10,
    width: 420,
    maxWidth: "calc(100vw - 32px)",
    boxShadow: "0 20px 60px rgba(0,0,0,0.2)",
    overflow: "hidden",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "16px 20px",
    borderBottom: "1px solid #e5e7eb",
  },
  title: {
    fontWeight: 700,
    fontSize: 15,
    color: "#111827",
  },
  closeBtn: {
    background: "none",
    border: "none",
    fontSize: 16,
    cursor: "pointer",
    color: "#6b7280",
    lineHeight: 1,
    padding: 2,
  },
  form: {
    padding: "20px",
    display: "flex",
    flexDirection: "column",
    gap: 14,
  },
  field: {
    display: "flex",
    flexDirection: "column",
    gap: 5,
  },
  label: {
    fontSize: 12,
    fontWeight: 600,
    color: "#374151",
    letterSpacing: 0.2,
  },
  required: {
    color: "#dc2626",
    marginLeft: 2,
  },
  input: {
    height: 36,
    padding: "0 10px",
    border: "1px solid #d1d5db",
    borderRadius: 6,
    fontSize: 13,
    color: "#111827",
    outline: "none",
    width: "100%",
    boxSizing: "border-box",
    background: "#fff",
  },
  actions: {
    display: "flex",
    justifyContent: "flex-end",
    gap: 8,
    marginTop: 4,
  },
  cancelBtn: {
    padding: "8px 16px",
    borderRadius: 6,
    fontSize: 13,
    cursor: "pointer",
    border: "1px solid #d1d5db",
    background: "#fff",
    color: "#374151",
    fontWeight: 500,
  },
  submitBtn: {
    padding: "8px 18px",
    borderRadius: 6,
    fontSize: 13,
    cursor: "pointer",
    border: "none",
    background: "#1d4ed8",
    color: "#fff",
    fontWeight: 600,
    transition: "opacity 0.15s",
  },
};

const toastStyles: Record<string, CSSProperties> = {
  stack: {
    position: "fixed",
    bottom: 24,
    right: 24,
    display: "flex",
    flexDirection: "column",
    gap: 8,
    zIndex: 2000,
  },
  toast: {
    color: "#fff",
    padding: "10px 16px",
    borderRadius: 8,
    fontSize: 13,
    fontWeight: 500,
    boxShadow: "0 4px 12px rgba(0,0,0,0.2)",
    maxWidth: 340,
    animation: "fadeIn 0.2s ease",
  },
};
