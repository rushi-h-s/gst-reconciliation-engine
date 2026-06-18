import { useState, useRef, CSSProperties } from "react";
import ReconciliationGrid from "./pages/ReconciliationGrid";

const ORG_ID =
  import.meta.env.VITE_ORG_ID ?? "00000000-0000-0000-0000-000000000000";
const JWT_TOKEN: string = import.meta.env.VITE_JWT_TOKEN ?? "";

type UploadPhase = "idle" | "uploading" | "done" | "error";
type ReconcilePhase = "idle" | "running" | "done" | "error";

function currentYearMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export default function App() {
  // Form state (draft — not yet applied to grid)
  const [clientId, setClientId] = useState("");
  const [period, setPeriod] = useState(currentYearMonth);

  // Applied state (drives the grid)
  const [activeClientId, setActiveClientId] = useState("");
  const [activePeriod, setActivePeriod] = useState("");
  const [gridKey, setGridKey] = useState(0); // increment to force grid re-mount

  // Upload feedback
  const [invPhase, setInvPhase] = useState<UploadPhase>("idle");
  const [gstrPhase, setGstrPhase] = useState<UploadPhase>("idle");
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);

  // Reconcile feedback
  const [recoPhase, setRecoPhase] = useState<ReconcilePhase>("idle");
  const [recoMsg, setRecoMsg] = useState<string | null>(null);

  const invRef = useRef<HTMLInputElement>(null);
  const gstrRef = useRef<HTMLInputElement>(null);
  const baseHeaders = { Authorization: `Bearer ${JWT_TOKEN}` };

  // ── Handlers ───────────────────────────────────────────────────

  function handleLoad() {
    const cid = clientId.trim();
    if (!cid || !period) return;
    setActiveClientId(cid);
    setActivePeriod(period);
    setRecoPhase("idle");
    setRecoMsg(null);
    setUploadMsg(null);
    setGridKey((k) => k + 1);
  }

  async function uploadInvoice(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!clientId.trim() || !period) {
      setUploadMsg("Set Client ID and Period before uploading.");
      return;
    }
    setInvPhase("uploading");
    setUploadMsg(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(
        `/api/v1/invoices/upload?client_id=${encodeURIComponent(clientId.trim())}&period=${period}`,
        { method: "POST", headers: baseHeaders, body: form }
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? res.statusText);
      setInvPhase("done");
      setUploadMsg(
        `Invoice queued (${data.status}) — extraction_id: ${data.extraction_id}`
      );
    } catch (err) {
      setInvPhase("error");
      setUploadMsg(`Invoice upload failed: ${(err as Error).message}`);
    }
  }

  async function uploadGstr2b(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!clientId.trim() || !period) {
      setUploadMsg("Set Client ID and Period before uploading.");
      return;
    }
    setGstrPhase("uploading");
    setUploadMsg(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const endpoint = file.name.endsWith(".xlsx")
        ? `/api/v1/gstr2b/upload-excel?client_id=${encodeURIComponent(clientId.trim())}&period=${period}`
        : `/api/v1/gstr2b/upload?client_id=${encodeURIComponent(clientId.trim())}&period=${period}`;
      const res = await fetch(endpoint, { method: "POST", headers: baseHeaders, body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? res.statusText);
      setGstrPhase("done");
      const sectionSummary = data.by_section
        ? Object.entries(data.by_section as Record<string, number>)
            .map(([k, n]) => `${k.toUpperCase()}:${n}`)
            .join(" ")
        : null;
      setUploadMsg(
        `GSTR-2B uploaded — ${data.inserted} entries${sectionSummary ? ` (${sectionSummary})` : ""}${data.warning ? ` — ${data.warning}` : ""}`
      );
    } catch (err) {
      setGstrPhase("error");
      setUploadMsg(`GSTR-2B upload failed: ${(err as Error).message}`);
    }
  }

  async function runReconciliation() {
    if (!activeClientId || !activePeriod) return;
    setRecoPhase("running");
    setRecoMsg(null);
    try {
      const res = await fetch(
        `/api/v1/reconciliation/run?client_id=${encodeURIComponent(activeClientId)}&period=${activePeriod}`,
        { method: "POST", headers: baseHeaders }
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? res.statusText);
      setRecoPhase("done");
      setRecoMsg(`Reconciled ${data.count} entries — grid refreshed`);
      setGridKey((k) => k + 1);
    } catch (err) {
      setRecoPhase("error");
      setRecoMsg(`Reconciliation failed: ${(err as Error).message}`);
    }
  }

  // ── Render ─────────────────────────────────────────────────────

  const canLoad = clientId.trim().length > 0 && period.length > 0;
  const canReco = !!activeClientId && recoPhase !== "running";
  const uploadError = invPhase === "error" || gstrPhase === "error";

  return (
    <div style={s.root}>
      {/* ── Header ────────────────────────────────────────────── */}
      <header style={s.header}>
        <div style={s.headerLeft}>
          <div style={s.logoBox}>GST</div>
          <div>
            <div style={s.appName}>GST Reconciliation Engine</div>
            <div style={s.appSub}>ITC Matching · Maker-Checker Review</div>
          </div>
        </div>
        <div style={s.orgBadge} title={ORG_ID}>
          Org {ORG_ID.slice(0, 8)}&hellip;
        </div>
      </header>

      {/* ── Control bar ───────────────────────────────────────── */}
      <div style={s.controlBar}>
        <div style={s.controlRow}>
          {/* Client ID */}
          <div style={s.field}>
            <label style={s.fieldLabel}>Client ID</label>
            <input
              style={s.input}
              placeholder="paste client UUID"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && canLoad && handleLoad()}
            />
          </div>

          {/* Period */}
          <div style={s.field}>
            <label style={s.fieldLabel}>Period</label>
            <input
              type="month"
              style={{ ...s.input, width: 148 }}
              value={period}
              onChange={(e) => setPeriod(e.target.value)}
            />
          </div>

          {/* Load */}
          <button
            style={{ ...s.btn, ...s.btnBlue, marginTop: 20, opacity: canLoad ? 1 : 0.45 }}
            disabled={!canLoad}
            onClick={handleLoad}
          >
            Load Grid
          </button>

          <div style={s.sep} />

          {/* Upload invoice */}
          <input
            ref={invRef}
            type="file"
            accept="image/jpeg,image/png,image/webp,image/tiff,application/pdf"
            style={{ display: "none" }}
            onChange={uploadInvoice}
          />
          <button
            style={{
              ...s.btn,
              ...s.btnOutline,
              marginTop: 20,
              opacity: invPhase === "uploading" ? 0.55 : 1,
            }}
            disabled={invPhase === "uploading"}
            onClick={() => invRef.current?.click()}
          >
            {invPhase === "uploading" ? "Uploading…" : "Upload Invoice"}
          </button>

          {/* Upload GSTR-2B */}
          <input
            ref={gstrRef}
            type="file"
            accept="application/json,.json,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            style={{ display: "none" }}
            onChange={uploadGstr2b}
          />
          <button
            style={{
              ...s.btn,
              ...s.btnOutline,
              marginTop: 20,
              opacity: gstrPhase === "uploading" ? 0.55 : 1,
            }}
            disabled={gstrPhase === "uploading"}
            onClick={() => gstrRef.current?.click()}
          >
            {gstrPhase === "uploading" ? "Uploading…" : "Upload GSTR-2B"}
          </button>

          {/* Re-reconcile */}
          <button
            style={{
              ...s.btn,
              ...s.btnGreen,
              marginTop: 20,
              opacity: canReco ? 1 : 0.45,
            }}
            disabled={!canReco}
            onClick={runReconciliation}
          >
            {recoPhase === "running" ? "Running…" : "Re-Reconcile"}
          </button>
        </div>

        {/* Status strip */}
        {uploadMsg && (
          <div style={{ ...s.statusStrip, color: uploadError ? "#dc2626" : "#15803d" }}>
            {uploadMsg}
          </div>
        )}
        {recoMsg && (
          <div
            style={{
              ...s.statusStrip,
              color: recoPhase === "error" ? "#dc2626" : "#1d4ed8",
            }}
          >
            {recoMsg}
          </div>
        )}

        {/* Active period indicator */}
        {activeClientId && (
          <div style={s.activePill}>
            Showing: client&nbsp;
            <strong>{activeClientId.slice(0, 8)}&hellip;</strong>
            &nbsp;&middot;&nbsp;
            <strong>{activePeriod}</strong>
          </div>
        )}
      </div>

      {/* ── Main content ──────────────────────────────────────── */}
      <main style={s.main}>
        {activeClientId && activePeriod ? (
          <ReconciliationGrid
            key={gridKey}
            orgId={ORG_ID}
            clientId={activeClientId}
            period={activePeriod}
          />
        ) : (
          <div style={s.empty}>
            <div style={s.emptyIcon}>[ ]</div>
            <div style={s.emptyTitle}>No period loaded</div>
            <div style={s.emptyHint}>
              Enter a Client ID and Period above, then click{" "}
              <strong>Load Grid</strong>.
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

// ── Styles ──────────────────────────────────────────────────────

const s: Record<string, CSSProperties> = {
  root: {
    minHeight: "100vh",
    background: "#f1f5f9",
    fontFamily: "system-ui, -apple-system, sans-serif",
  },

  // Header
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "14px 24px",
    background: "#1e3a8a",
    color: "#fff",
  },
  headerLeft: { display: "flex", alignItems: "center", gap: 12 },
  logoBox: {
    width: 40,
    height: 40,
    borderRadius: 8,
    background: "#fff",
    color: "#1e3a8a",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontWeight: 900,
    fontSize: 13,
    letterSpacing: 0.5,
    flexShrink: 0,
  },
  appName: { fontWeight: 700, fontSize: 16 },
  appSub: { fontSize: 11, color: "#93c5fd", marginTop: 1 },
  orgBadge: {
    fontSize: 11,
    background: "rgba(255,255,255,0.12)",
    padding: "4px 10px",
    borderRadius: 20,
    cursor: "default",
  },

  // Control bar
  controlBar: {
    background: "#fff",
    borderBottom: "1px solid #e2e8f0",
    padding: "16px 24px 12px",
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  controlRow: {
    display: "flex",
    alignItems: "flex-end",
    gap: 10,
    flexWrap: "wrap",
  },
  field: { display: "flex", flexDirection: "column", gap: 4 },
  fieldLabel: { fontSize: 11, fontWeight: 600, color: "#64748b", letterSpacing: 0.3 },
  input: {
    height: 34,
    padding: "0 10px",
    border: "1px solid #cbd5e1",
    borderRadius: 6,
    fontSize: 13,
    color: "#1e293b",
    outline: "none",
    width: 240,
    background: "#fff",
  },
  sep: {
    width: 1,
    height: 34,
    background: "#e2e8f0",
    alignSelf: "flex-end",
    marginBottom: 0,
  },

  // Buttons
  btn: {
    height: 34,
    padding: "0 16px",
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 500,
    cursor: "pointer",
    border: "none",
    whiteSpace: "nowrap" as const,
    transition: "opacity 0.15s",
  },
  btnBlue: { background: "#1d4ed8", color: "#fff" },
  btnGreen: { background: "#15803d", color: "#fff" },
  btnOutline: {
    background: "#fff",
    border: "1px solid #cbd5e1",
    color: "#374151",
  },

  // Status / indicators
  statusStrip: { fontSize: 12, padding: "2px 0" },
  activePill: {
    fontSize: 12,
    color: "#475569",
    background: "#f1f5f9",
    border: "1px solid #e2e8f0",
    borderRadius: 20,
    padding: "3px 12px",
    alignSelf: "flex-start",
  },

  // Main / empty
  main: { padding: "0 8px 32px" },
  empty: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    minHeight: 340,
    color: "#94a3b8",
    gap: 12,
  },
  emptyIcon: {
    fontSize: 36,
    fontFamily: "monospace",
    color: "#cbd5e1",
  },
  emptyTitle: { fontSize: 16, fontWeight: 600, color: "#64748b" },
  emptyHint: { fontSize: 13, textAlign: "center" as const, maxWidth: 340 },
};
