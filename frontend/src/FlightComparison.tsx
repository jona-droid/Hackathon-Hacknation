import { useEffect, useState } from "react";

type SessionMeta = { session_id: string; mode: string; has_summary?: boolean };

type ComparisonReport = {
  expert_session_id: string;
  novice_session_id: string;
  overall_score: number;
  coverage_comparison: {
    expert_inspected_count: number;
    novice_inspected_count: number;
    missed_targets: string[];
    commentary: string;
  };
  safety_compliance: {
    min_cable_distance_expert: number;
    min_cable_distance_novice: number;
    violations_novice: number;
    compliance_rating: string;
    commentary: string;
  };
  technique_and_stability: {
    flight_duration_ratio: number;
    hover_discipline: string;
    knowledge_adherence: string;
  };
  key_strengths: string[];
  areas_for_improvement: string[];
  instructor_verdict: string;
};

export function FlightComparison(props: {
  currentSessionId: string | null;
  onReplay?: (t: number) => void;
}) {
  const { currentSessionId } = props;
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [selectedExpert, setSelectedExpert] = useState<string>("");
  const [selectedNovice, setSelectedNovice] = useState<string>("");
  const [currentSummary, setCurrentSummary] = useState<any>(null);
  const [comparison, setComparison] = useState<ComparisonReport | null>(null);
  const [loading, setLoading] = useState<boolean>(false);

  const fetchSessions = async () => {
    try {
      const res = await fetch("http://localhost:8000/sessions");
      const data = await res.json();
      const list: SessionMeta[] = data.sessions || [];
      setSessions(list);

      const experts = list.filter((s) => s.mode === "expert");
      const novices = list.filter((s) => s.mode === "novice" || s.mode === "tutor");

      if (experts.length > 0 && !selectedExpert) setSelectedExpert(experts[0].session_id);
      if (novices.length > 0 && !selectedNovice) setSelectedNovice(novices[0].session_id);
    } catch (e) {
      console.error("Failed to load sessions:", e);
    }
  };

  const fetchCurrentSummary = async () => {
    if (!currentSessionId) return;
    try {
      const res = await fetch(`http://localhost:8000/session/${currentSessionId}/summary`);
      if (res.ok) {
        const data = await res.json();
        setCurrentSummary(data.summary);
      }
    } catch {
      // Summary not generated yet
    }
  };

  useEffect(() => {
    fetchSessions();
    fetchCurrentSummary();
  }, [currentSessionId]);

  const handleCompare = async () => {
    if (!selectedExpert || !selectedNovice) {
      alert("Please select both an Expert session and a Novice session to compare.");
      return;
    }
    setLoading(true);
    try {
      const res = await fetch("http://localhost:8000/session/compare", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expert_session_id: selectedExpert,
          novice_session_id: selectedNovice,
        }),
      });
      const data = await res.json();
      if (data.comparison) {
        setComparison(data.comparison);
      }
    } catch (e) {
      console.error("Comparison error:", e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flight-comparison">
      {/* Current Flight Summary */}
      {currentSummary && (
        <div className="summary-card">
          <div className="summary-title">
            <span>🏁 Flight Debrief: {currentSummary.session_id}</span>
            <span className="badge">{currentSummary.operator_type?.toUpperCase()}</span>
          </div>
          <div className="metrics-grid">
            <div className="metric">
              <span className="m-val">{currentSummary.flight_duration_sec}s</span>
              <span className="m-lbl">Duration</span>
            </div>
            <div className="metric">
              <span className="m-val">{currentSummary.inspection_coverage_percent}%</span>
              <span className="m-lbl">Coverage</span>
            </div>
            <div className="metric">
              <span className="m-val">{currentSummary.min_cable_distance}m</span>
              <span className="m-lbl">Min Clearance</span>
            </div>
            <div className="metric">
              <span className="m-val">{currentSummary.safety_violations_count}</span>
              <span className="m-lbl">Alerts</span>
            </div>
          </div>
          <p className="summary-text">{currentSummary.operational_summary}</p>
        </div>
      )}

      {/* Comparison Selector */}
      <div className="compare-controls">
        <div className="selector-title">
          <strong>Compare Operator Flights</strong>
          <span>Evaluate Novice adherence against Expert benchmark</span>
        </div>

        <div className="selector-row">
          <div className="select-group">
            <label>Master Flight (Expert):</label>
            <select value={selectedExpert} onChange={(e) => setSelectedExpert(e.target.value)}>
              <option value="">Select Expert...</option>
              {sessions
                .filter((s) => s.mode === "expert")
                .map((s) => (
                  <option key={s.session_id} value={s.session_id}>
                    {s.session_id}
                  </option>
                ))}
            </select>
          </div>

          <div className="select-group">
            <label>Apprentice Flight (Novice):</label>
            <select value={selectedNovice} onChange={(e) => setSelectedNovice(e.target.value)}>
              <option value="">Select Novice...</option>
              {sessions
                .filter((s) => s.mode === "novice" || s.mode === "tutor")
                .map((s) => (
                  <option key={s.session_id} value={s.session_id}>
                    {s.session_id}
                  </option>
                ))}
            </select>
          </div>

          <button className="btn-primary" onClick={handleCompare} disabled={loading}>
            {loading ? "Analyzing..." : "Compare Performance"}
          </button>
        </div>
      </div>

      {/* Comparison Report Display */}
      {comparison && (
        <div className="comparison-report">
          <div className="report-header">
            <div>
              <h3>Instructor Evaluation Report</h3>
              <span className="session-pair">
                {comparison.expert_session_id} ➔ {comparison.novice_session_id}
              </span>
            </div>
            <div className="score-badge">
              <span className="score-number">{comparison.overall_score}</span>
              <span className="score-max">/ 100</span>
            </div>
          </div>

          <div className="report-section">
            <h4>1. Safety Compliance &amp; Standoff Margins</h4>
            <div className={`compliance-tag ${comparison.safety_compliance.compliance_rating.toLowerCase().replace(/\s+/g, "-")}`}>
              Rating: {comparison.safety_compliance.compliance_rating}
            </div>
            <p>{comparison.safety_compliance.commentary}</p>
            <div className="stat-comparison">
              <div>Expert Min Clearance: <strong>{comparison.safety_compliance.min_cable_distance_expert}m</strong></div>
              <div>Novice Min Clearance: <strong>{comparison.safety_compliance.min_cable_distance_novice}m</strong></div>
              <div>Novice Violations: <strong>{comparison.safety_compliance.violations_novice}</strong></div>
            </div>
          </div>

          <div className="report-section">
            <h4>2. Inspection Coverage</h4>
            <p>{comparison.coverage_comparison.commentary}</p>
            {comparison.coverage_comparison.missed_targets.length > 0 && (
              <div className="missed-targets">
                Missed Insulators: {comparison.coverage_comparison.missed_targets.join(", ")}
              </div>
            )}
          </div>

          <div className="report-section">
            <h4>3. Technique &amp; Knowledge Base Adherence</h4>
            <p><strong>Hover Discipline:</strong> {comparison.technique_and_stability.hover_discipline}</p>
            <p><strong>Adherence to knowledge.md:</strong> {comparison.technique_and_stability.knowledge_adherence}</p>
          </div>

          <div className="report-two-col">
            <div className="col strengths">
              <h4>Key Strengths</h4>
              <ul>
                {comparison.key_strengths.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </div>
            <div className="col improvements">
              <h4>Areas for Improvement</h4>
              <ul>
                {comparison.areas_for_improvement.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            </div>
          </div>

          <div className="instructor-verdict">
            <h4>Chief Instructor Verdict</h4>
            <p>{comparison.instructor_verdict}</p>
          </div>
        </div>
      )}
    </div>
  );
}
