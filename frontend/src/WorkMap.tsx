import { useEffect, useState } from "react";

type WorkMapData = {
  work_map: { title: string; steps: Array<{ id: number; t_start: number; action: string; decision: string; reason: string | null; exceptions: string | null; confidence: string }> };
  guardrails: Array<Record<string, unknown>>;
  open_questions: string[];
};

export function WorkMap(props: { sessionId: string | null; onReplay: (t: number) => void; onGuardrailsSaved: (g: Array<Record<string, unknown>>) => void }) {
  const { sessionId, onReplay, onGuardrailsSaved } = props;
  const [data, setData] = useState<WorkMapData | null>(null);
  const [guardrailText, setGuardrailText] = useState("[]");

  useEffect(() => {
    if (!data) return;
    setGuardrailText(JSON.stringify(data.guardrails, null, 2));
  }, [data]);

  const generate = async () => {
    if (!sessionId) return;
    const res = await fetch(`http://localhost:8000/session/${sessionId}/workmap`, { method: "POST" });
    const body = await res.json();
    setData(body);
  };

  const saveGuardrails = async () => {
    const guardrails = JSON.parse(guardrailText);
    await fetch("http://localhost:8000/guardrails", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guardrails }),
    });
    onGuardrailsSaved(guardrails);
  };

  return (
    <div>
      <button onClick={generate} disabled={!sessionId}>Generate Work Map</button>
      {data && (
        <>
          <h3>{data.work_map.title}</h3>
          {data.work_map.steps.map((s) => (
            <div key={s.id} className="step" onClick={() => onReplay(s.t_start)}>
              <div><strong>{s.id}.</strong> {s.action}</div>
              <div>Decision: {s.decision}</div>
              <div>Reason: {s.reason ?? "(none)"}</div>
              <div>Exceptions: {s.exceptions ?? "(none)"}</div>
              <span className="badge">{s.confidence}</span>
            </div>
          ))}
          <h4>Guardrails</h4>
          <textarea value={guardrailText} onChange={(e) => setGuardrailText(e.target.value)} rows={10} />
          <button onClick={saveGuardrails}>Save</button>
        </>
      )}
    </div>
  );
}
