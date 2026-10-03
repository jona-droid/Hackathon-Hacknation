import { useEffect, useState } from "react";
import { API_URL } from "./config";

export function KnowledgeViewer(props: { refreshKey?: number }) {
  const { refreshKey } = props;
  const [content, setContent] = useState<string>("");
  const [isEditing, setIsEditing] = useState<boolean>(false);
  const [editText, setEditText] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(false);
  const [savedSuccess, setSavedSuccess] = useState<boolean>(false);

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
    fetchKnowledge();
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
          <button className="btn-sm" onClick={fetchKnowledge} disabled={loading}>
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
