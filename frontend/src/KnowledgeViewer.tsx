import { useEffect, useState } from "react";
import { API_URL } from "./config";
import { KnowledgeCoverage } from "./useSimSocket";

type SlotEntry = {
  rule: string;
  conditions?: string[];
  confidence: "once" | "confirmed";
  confirmations?: number;
  evidence?: Record<string, number>;
};
type GridView = {
  coverage: KnowledgeCoverage;
  tasks: Array<{
    id: string;
    name: string;
    slots: Array<{ key: string; label: string; type: string; learn: string; entry: SlotEntry | null }>;
  }>;
};

/** The apprentice's competence grid: which slots are learned (amber), confirmed in later flights (green) or empty. */
function CompetenceGrid({ grid, onReset }: { grid: GridView; onReset: () => void }) {
  const [open, setOpen] = useState<string | null>(null);
  const slot = grid.tasks.flatMap((t) => t.slots).find((s) => s.key === open);
  const { filled, confirmed, total } = grid.coverage;

  return (
    <div className="grid-box">
      <div className="grid-head">
        <strong>Competence grid</strong>
        <span>
          {filled}/{total} learned · {confirmed} confirmed in flight
        </span>
        <button className="btn-sm" onClick={onReset}>
          Reset grid
        </button>
      </div>
      {grid.tasks.map((t) => (
        <div key={t.id} className="grid-task">
          <span className="grid-task-name">{t.name}</span>
          <div className="grid-slots">
            {t.slots.map((s) => (
              <button
                key={s.key}
                className={`slot-chip ${s.entry?.confidence ?? "empty"} ${open === s.key ? "open" : ""}`}
                onClick={() => setOpen(open === s.key ? null : s.key)}
              >
                {s.label}
                {s.entry?.confidence === "confirmed" && ` ×${s.entry.confirmations}`}
              </button>
            ))}
          </div>
        </div>
      ))}
      {slot && (
        <div className="slot-detail">
          <strong>{slot.label}</strong>
          {slot.entry ? (
            <>
              <p>{slot.entry.rule}</p>
              {slot.entry.conditions?.map((c) => (
                <p key={c} className="slot-condition">↳ {c}</p>
              ))}
              {slot.entry.evidence && Object.keys(slot.entry.evidence).length > 0 && (
                <p className="slot-evidence">
                  Measured:{" "}
                  {Object.entries(slot.entry.evidence)
                    .map(([k, v]) => `${k.replace(/_(median|min|max|s)$/, "").replace(/_/g, " ")} ${v}`)
                    .join(", ")}
                </p>
              )}
            </>
          ) : (
            <p className="slot-empty">Not learned yet: {slot.learn}</p>
          )}
        </div>
      )}
    </div>
  );
}

export function KnowledgeViewer(props: { refreshKey?: number }) {
  const { refreshKey } = props;
  const [content, setContent] = useState<string>("");
  const [isEditing, setIsEditing] = useState<boolean>(false);
  const [editText, setEditText] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);
  const [savedSuccess, setSavedSuccess] = useState<boolean>(false);
  const [grid, setGrid] = useState<GridView | null>(null);

  const fetchGrid = () =>
    fetch(`${API_URL}/knowledge/competence`)
      .then((r) => r.json())
      .then(setGrid)
      .catch((e) => console.error("Failed to load the competence grid:", e));

  const resetGrid = async () => {
    if (!confirm("Empty the competence grid? Everything the apprentice learned in it is deleted.")) return;
    await fetch(`${API_URL}/knowledge/competence`, { method: "DELETE" }).catch(() => {});
    fetchGrid();
    fetchKnowledge();
  };

  const fetchKnowledge = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_URL}/knowledge`);
      const data = await res.json();
      setContent(data.content || "");
      setEditText(data.content || "");
    } catch (e) {
      console.error("Failed to load knowledge.md:", e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchGrid();
    if (!isEditing) fetchKnowledge(); // don't overwrite an edit in progress
  }, [refreshKey]);

  const handleSave = async () => {
    try {
      const res = await fetch(`${API_URL}/knowledge`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: editText }),
      });
      if (res.ok) {
        setContent(editText);
        setIsEditing(false);
        setSavedSuccess(true);
        setTimeout(() => setSavedSuccess(false), 3000);
      }
    } catch (e) {
      console.error("Failed to save knowledge.md:", e);
    }
  };

  return (
    <div className="knowledge-viewer">
      <div className="kb-header">
        <div className="kb-title">
          <strong>📘 knowledge.md</strong>
          <span className="kb-subtitle">Living Expert Knowledge Base</span>
        </div>
        <div className="kb-actions">
          {savedSuccess && <span className="save-badge">✓ Saved!</span>}
          <button className="btn-sm" onClick={() => { fetchKnowledge(); fetchGrid(); }} disabled={loading}>
            ↻ Refresh
          </button>
          {isEditing ? (
            <>
              <button className="btn-primary btn-sm" onClick={handleSave}>
                Save Changes
              </button>
              <button className="btn-sm" onClick={() => setIsEditing(false)}>
                Cancel
              </button>
            </>
          ) : (
            <button className="btn-sm" onClick={() => setIsEditing(true)}>
              Edit File
            </button>
          )}
        </div>
      </div>

      {grid && <CompetenceGrid grid={grid} onReset={resetGrid} />}

      {isEditing ? (
        <textarea
          className="kb-editor"
          value={editText}
          onChange={(e) => setEditText(e.target.value)}
          rows={24}
        />
      ) : (
        <div className="kb-content">
          <pre>{content || "Loading knowledge.md..."}</pre>
        </div>
      )}
    </div>
  );
}
