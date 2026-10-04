import { useEffect, useRef, useState } from "react";
import { AIAdvice, AIObservation, AIQuestion, Attention, Guardian, Prediction } from "./useSimSocket";
import { API_URL } from "./config";
import { AnswerRecording, NOTE_LIMITS, audioContext, beep, recordAnswer } from "./recordAnswer";
import { VoiceViz } from "./VoiceViz";

type Role = "ai" | "pilot" | "tutor" | "system" | "learned";
type TranscriptRow = { role: Role; text: string; t?: number; tag?: string; high?: boolean };
type QuestionMeta = { slotName?: string | null; kind?: AIQuestion["kind"] };
type AnswerResult = {
  insight?: string | null;
  rejected?: boolean;
  slot_name?: string | null;
  follow_up?: string;
  error?: string;
};

const KIND_LABEL: Record<string, string> = {
  rule: "New rule",
  hypothesis: "Habit check",
  deviation: "Rule check",
  follow_up: "Follow-up",
};
const ROLE_LABEL: Record<Role, string> = { ai: "Apprentice", pilot: "You", tutor: "Tutor", system: "System", learned: "Learned" };

function useTypewriter(text: string, charsPerSecond = 60): string {
  const [n, setN] = useState(0);
  useEffect(() => {
    setN(0);
    if (!text) return;
    const id = setInterval(() => {
      setN((v) => {
        if (v >= text.length) {
          clearInterval(id);
          return v;
        }
        return v + 1;
      });
    }, 1000 / charsPerSecond);
    return () => clearInterval(id);
  }, [text]);
  return text.slice(0, n);
}

// Voice loop (expert mode): Claude's question is spoken with ElevenLabs text-to-speech, then the mic
// opens for one answer only, closes on silence, and the audio is transcribed by ElevenLabs on the backend.
// The pilot can also record a note on their own (button or R): the apprentice holds its questions meanwhile.
export function VoicePanel(props: {
  mode: "expert" | "novice";
  sessionId: string | null;
  simTime: number;
  latestQuestion: AIQuestion | null;
  latestAdvice: AIAdvice | null;
  latestObservation?: AIObservation | null;
  attention?: Attention | null;
  guardian?: Guardian;
  prediction?: Prediction | null;
  sessionActive: boolean;
  hidden?: boolean;
  onKnowledgeUpdated?: () => void;
}) {
  const {
    mode,
    sessionId,
    simTime,
    latestQuestion,
    latestAdvice,
    latestObservation,
    attention,
    guardian,
    sessionActive,
    hidden,
    onKnowledgeUpdated,
  } = props;

  const [transcript, setTranscript] = useState<TranscriptRow[]>([]);
  const [currentQuestion, setCurrentQuestion] = useState<string>("");
  const [answerInput, setAnswerInput] = useState<string>("");
  const [noviceQueryInput, setNoviceQueryInput] = useState<string>("");
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [lastSavedInsight, setLastSavedInsight] = useState<{ slot?: string | null; text: string } | null>(null);
  const [currentMeta, setCurrentMeta] = useState<QuestionMeta>({});
  const [audioPlaying, setAudioPlaying] = useState<boolean>(false);
  const [recording, setRecording] = useState<"answer" | "note" | "question" | null>(null); // what the open mic records
  const listening = recording !== null;
  const typed = useTypewriter(currentQuestion);

  const lastQuestionRef = useRef<string>("");
  const lastAdviceRef = useRef<AIAdvice | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const speakAnalyser = useRef<AnalyserNode | null>(null);
  const speechGenRef = useRef(0); // bumped to cancel speech that is still being fetched
  const playbackEndRef = useRef<((finished: boolean) => void) | null>(null);
  const recordingRef = useRef<AnswerRecording | null>(null);
  const noteActiveRef = useRef(false); // from Record a note until the note is sent or discarded
  const wasActiveRef = useRef(sessionActive);
  const logRef = useRef<HTMLDivElement | null>(null);
  // read by the async voice loop, which outlives the render it started in
  const liveRef = useRef({ sessionActive, currentQuestion });
  liveRef.current = { sessionActive, currentQuestion };

  const addRow = (row: TranscriptRow) => setTranscript((prev) => [...prev.slice(-60), row]);
  useEffect(() => {
    const el = logRef.current; // scroll the log itself, not the whole side panel
    if (el) el.scrollTop = el.scrollHeight;
  }, [transcript.length]);

  // ---- speaking (ElevenLabs text-to-speech, browser voice as fallback) ----
  const stopSpeaking = () => {
    speechGenRef.current += 1;
    audioRef.current?.pause();
    audioRef.current = null;
    speakAnalyser.current = null;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    playbackEndRef.current?.(false);
    playbackEndRef.current = null;
    setAudioPlaying(false);
  };

  /** Route the clip through an analyser for the visualiser (only when audio is unlocked, else play it plainly). */
  const analyse = (audio: HTMLAudioElement) => {
    const ctx = audioContext();
    if (!ctx || ctx.state !== "running") return;
    try {
      const source = ctx.createMediaElementSource(audio);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      analyser.connect(ctx.destination);
      speakAnalyser.current = analyser;
    } catch {
      speakAnalyser.current = null;
    }
  };

  /** Resolves true once the text has been fully spoken, false if stopped or replaced. */
  const speakText = async (text: string): Promise<boolean> => {
    if (!text) return false;
    stopSpeaking(); // never talk over the previous clip
    const gen = speechGenRef.current;
    const finished = new Promise<boolean>((resolve) => {
      playbackEndRef.current = resolve;
    });
    const end = (ok: boolean) => {
      if (gen !== speechGenRef.current) return;
      playbackEndRef.current?.(ok);
      playbackEndRef.current = null;
      speakAnalyser.current = null;
      setAudioPlaying(false);
    };
    setAudioPlaying(true);
    try {
      const res = await fetch(`${API_URL}/elevenlabs/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error(`TTS HTTP ${res.status}`);
      const blob = await res.blob();
      if (gen !== speechGenRef.current) return false; // stopped or replaced while fetching
      const audio = new Audio(URL.createObjectURL(blob));
      audioRef.current = audio;
      analyse(audio);
      audio.onended = () => end(true);
      audio.onerror = () => end(false);
      await audio.play();
    } catch {
      if (gen !== speechGenRef.current) return false;
      if ("speechSynthesis" in window) {
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.onend = () => end(true);
        utterance.onerror = () => end(false);
        window.speechSynthesis.speak(utterance);
      } else {
        end(false);
      }
    }
    return finished;
  };

  const voiceLevel = () => {
    if (recordingRef.current) return recordingRef.current.level();
    const an = speakAnalyser.current;
    if (!an) return audioPlaying ? 0.25 + 0.25 * Math.random() : 0;
    const buf = new Float32Array(an.fftSize);
    an.getFloatTimeDomainData(buf);
    return Math.min(1, Math.sqrt(buf.reduce((s, v) => s + v * v, 0) / buf.length) * 6);
  };

  // ---- answers ----
  const showAnswerResult = (question: string, answer: string, data: AnswerResult) => {
    addRow({ role: "pilot", text: answer, t: simTime, tag: question ? "Answer" : "Note" });
    if (data.error) {
      addRow({ role: "system", text: `Not saved: the knowledge model failed (${data.error}).` });
    } else if (data.rejected) {
      addRow({
        role: "system",
        text: data.follow_up
          ? "Not saved yet: too vague. A follow-up question will come at a calm moment."
          : "Not saved: no usable know-how in that answer.",
      });
    } else if (data.insight) {
      setLastSavedInsight({ slot: data.slot_name, text: data.insight });
      addRow({ role: "learned", text: `${data.slot_name ? `${data.slot_name} — ` : ""}${data.insight}` });
      if (data.follow_up) addRow({ role: "system", text: "Saved; a follow-up question will make it more precise." });
      onKnowledgeUpdated?.();
    }
    setCurrentQuestion((q) => (q === question ? "" : q)); // answered; wait for the next one
  };

  const skipQuestion = async (question: string, reason: string) => {
    await fetch(`${API_URL}/dialogue/skip`, { method: "POST" }).catch(() => {});
    setCurrentQuestion((q) => (q === question ? "" : q));
    addRow({ role: "system", text: reason });
  };

  /** Beep, open the mic and wait until silence, Stop (Enter) or Discard (Esc). Null = nothing kept. */
  const record = async (kind: "answer" | "note" | "question"): Promise<Blob | null> => {
    let rec: AnswerRecording;
    try {
      beep();
      await new Promise((r) => setTimeout(r, 250)); // don't record the beep
      rec = await recordAnswer(kind === "note" ? NOTE_LIMITS : undefined);
    } catch (err) {
      addRow({ role: "system", text: `Microphone unavailable (${(err as Error)?.message ?? err}). Type it instead.` });
      return null;
    }
    recordingRef.current = rec;
    setRecording(kind);
    const audio = await rec.done;
    setRecording(null);
    if (recordingRef.current === rec) recordingRef.current = null;
    return audio;
  };

  /** Transcribe and learn from a recording; an empty question means the pilot's own note. */
  const sendVoice = async (audio: Blob, question: string) => {
    setIsSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/dialogue/voice-answer?question=${encodeURIComponent(question)}`, {
        method: "POST",
        headers: { "Content-Type": audio.type || "audio/webm" },
        body: audio,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? `HTTP ${res.status}`);
      if (!data.transcript) {
        addRow({ role: "system", text: question ? "Nothing understood in the recording: question skipped." : "Nothing understood in the note." });
        if (question) setCurrentQuestion((q) => (q === question ? "" : q));
      } else {
        showAnswerResult(question, data.transcript, data);
      }
    } catch (err) {
      addRow({ role: "system", text: `Could not process the recording: ${(err as Error)?.message ?? err}` });
    } finally {
      setIsSubmitting(false);
    }
  };

  /** Mic on for one answer, then off. Enter / Stop = done, Esc / Discard = skip, silence = done. */
  const listenForAnswer = async (question: string) => {
    const audio = await record("answer");
    // flight ended, a new question arrived, a note was started, or the answer was typed meanwhile
    if (!liveRef.current.sessionActive || liveRef.current.currentQuestion !== question) return;
    if (!audio) {
      await skipQuestion(question, "No answer recorded: question skipped.");
      return;
    }
    await sendVoice(audio, question);
  };

  /** The pilot's own note, without a question. The apprentice holds its questions until it is sent. */
  const recordNote = async () => {
    if (!liveRef.current.sessionActive || recordingRef.current || noteActiveRef.current) return;
    noteActiveRef.current = true;
    try {
      await recordAndSendNote();
    } finally {
      noteActiveRef.current = false;
    }
  };
  const recordAndSendNote = async () => {
    stopSpeaking();
    const dropped = liveRef.current.currentQuestion;
    liveRef.current.currentQuestion = "";
    setCurrentQuestion("");
    if (dropped) addRow({ role: "system", text: "Question dropped: recording your note instead." });
    await fetch(`${API_URL}/dialogue/pilot-note?active=true`, { method: "POST" }).catch(() => {});

    const audio = await record("note");
    if (!audio || !liveRef.current.sessionActive) {
      await fetch(`${API_URL}/dialogue/pilot-note?active=false`, { method: "POST" }).catch(() => {});
      if (liveRef.current.sessionActive) addRow({ role: "system", text: "Note discarded." });
      return;
    }
    await sendVoice(audio, "");
  };
  const recordNoteRef = useRef(recordNote);
  recordNoteRef.current = recordNote;

  /** Novice: show the pilot's question and speak the tutor's answer. */
  const showTutorAnswer = (query: string, advice: Partial<AIAdvice>) => {
    addRow({ role: "pilot", text: query, t: simTime, tag: "Question" });
    if (advice.speech) {
      addRow({ role: "tutor", text: advice.speech, t: simTime, tag: "Answer" });
      speakText(advice.speech);
    }
  };

  /** Novice: ask the tutor out loud. Mic on until silence (Enter = send, Esc = cancel), then a spoken answer. */
  const askTutorByVoice = async () => {
    if (recordingRef.current || noteActiveRef.current) return;
    stopSpeaking(); // don't record the tutor's own voice
    const audio = await record("question");
    if (!audio) {
      addRow({ role: "system", text: "No question recorded." });
      return;
    }
    setIsSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/dialogue/voice-advise`, {
        method: "POST",
        headers: { "Content-Type": audio.type || "audio/webm" },
        body: audio,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? `HTTP ${res.status}`);
      if (!data.transcript) {
        addRow({ role: "system", text: "Nothing understood: ask again or type your question." });
      } else {
        showTutorAnswer(data.transcript, data);
      }
    } catch (err) {
      addRow({ role: "system", text: `Could not ask the tutor: ${(err as Error)?.message ?? err}` });
    } finally {
      setIsSubmitting(false);
    }
  };
  const askTutorByVoiceRef = useRef(askTutorByVoice);
  askTutorByVoiceRef.current = askTutorByVoice;

  /** New question: show it, speak it, then listen for the answer. */
  const askAndListen = async (question: string, t?: number, meta: QuestionMeta = {}) => {
    recordingRef.current?.cancel();
    lastQuestionRef.current = question;
    liveRef.current.currentQuestion = question;
    setCurrentQuestion(question);
    setCurrentMeta(meta);
    addRow({ role: "ai", text: question, t, tag: KIND_LABEL[meta.kind ?? "rule"] ?? "Question" });
    const spoken = await speakText(question);
    if (spoken && mode === "expert" && liveRef.current.sessionActive && liveRef.current.currentQuestion === question) {
      await listenForAnswer(question);
    }
  };

  // R (during a flight, not typing): record a note in expert mode, ask the tutor by voice in novice mode
  useEffect(() => {
    if (!sessionActive) return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || e.repeat) return;
      if (e.key.toLowerCase() === "r") (mode === "expert" ? recordNoteRef : askTutorByVoiceRef).current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode, sessionActive]);

  // Enter / Esc while the mic is open
  useEffect(() => {
    if (!listening) return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.key === "Enter") recordingRef.current?.finish();
      if (e.key === "Escape") recordingRef.current?.cancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [listening]);

  // Question pushed by the observer
  useEffect(() => {
    if (latestQuestion?.question && latestQuestion.question !== lastQuestionRef.current) {
      lastQuestionRef.current = latestQuestion.question;
      // a question sent just before Record a note: the backend already dropped it
      if (sessionActive && !noteActiveRef.current) {
        askAndListen(latestQuestion.question, latestQuestion.t, {
          slotName: latestQuestion.slot_name,
          kind: latestQuestion.kind,
        });
      }
    }
  }, [latestQuestion]);

  // Tutor advice (novice mode): spoken only. Compared by message, not text: a repeated alarm has the same words.
  useEffect(() => {
    if (latestAdvice?.speech && latestAdvice !== lastAdviceRef.current) {
      lastAdviceRef.current = latestAdvice;
      if (!sessionActive) return;
      addRow({ role: "tutor", text: latestAdvice.speech, t: simTime, tag: latestAdvice.category.replace("_", " "), high: latestAdvice.urgency === "high" });
      if (recordingRef.current) {
        if (latestAdvice.urgency !== "high") return; // shown, not spoken into the open mic
        recordingRef.current.cancel(); // safety first: drop the question being recorded
      }
      speakText(latestAdvice.speech);
    }
  }, [latestAdvice]);

  // End Flight stops everything: speech, microphone, pending question
  useEffect(() => {
    if (wasActiveRef.current && !sessionActive) {
      stopSpeaking();
      recordingRef.current?.cancel();
      setCurrentQuestion("");
      addRow({ role: "system", text: "Flight ended. The apprentice has stopped asking questions.", t: simTime });
    }
    wasActiveRef.current = sessionActive;
  }, [sessionActive]);

  // Expert Mode: Trigger manual question from AI
  const triggerQuestion = async () => {
    if (!sessionActive) return;
    try {
      const res = await fetch(`${API_URL}/dialogue/trigger-question`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: "manual_inquiry", t: simTime }),
      });
      const data = await res.json();
      if (!res.ok) {
        addRow({ role: "system", text: data.detail ?? "Could not get a question from the apprentice." });
        return;
      }
      if (data.question && liveRef.current.sessionActive) {
        askAndListen(data.question, data.t, { slotName: data.slot_name, kind: data.kind });
      }
    } catch (err) {
      console.error("Failed to trigger question:", err);
    }
  };

  // Expert Mode: typed answer (also cancels the mic if it is open)
  const submitAnswer = async () => {
    const ans = answerInput.trim();
    if (!ans) return;
    const question = currentQuestion;
    liveRef.current.currentQuestion = ""; // the voice loop must not also submit/skip this question
    recordingRef.current?.cancel();

    setIsSubmitting(true);
    try {
      const res = await fetch(`${API_URL}/dialogue/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, question, answer: ans }), // no question: a note
      });
      const data = await res.json();
      if (data.ok) {
        setAnswerInput("");
        showAnswerResult(question, ans, data);
      }
    } catch (err) {
      console.error("Failed to submit answer:", err);
    } finally {
      setIsSubmitting(false);
    }
  };

  // Novice Mode: Ask question to AI Tutor
  const askTutor = async () => {
    const query = noviceQueryInput.trim();
    if (!query) return;
    setNoviceQueryInput("");

    try {
      const res = await fetch(`${API_URL}/dialogue/advise`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, query }),
      });
      showTutorAnswer(query, await res.json());
    } catch (err) {
      console.error("Failed to ask tutor:", err);
    }
  };

  // ---- view ----
  const vizMode: "idle" | "speaking" | "listening" = listening ? "listening" : audioPlaying ? "speaking" : "idle";
  const voiceLabel =
    recording === "note"
      ? "Recording note"
      : recording === "question"
        ? "Listening"
        : recording === "answer"
          ? "Your answer"
          : isSubmitting
            ? "Processing"
            : audioPlaying
              ? mode === "expert" ? "Apprentice" : "Tutor"
              : "Voice idle";

  let brain = { label: "Standby", orb: "idle", sub: "Start a flight: the apprentice watches and learns." };
  if (sessionActive && mode === "expert") {
    const a = attention;
    if (a?.thinking) brain = { label: "Thinking…", orb: "thinking", sub: "Composing a question for this moment." };
    else if (recording === "answer") brain = { label: "Listening to you", orb: "", sub: "Enter = done · Esc = skip" };
    else if (audioPlaying) brain = { label: "Speaking", orb: "", sub: "Question on air." };
    else if (a?.waiting_answer) brain = { label: "Waiting for your answer", orb: "", sub: "Speak or type it below." };
    else if (a?.follow_up) brain = { label: "Follow-up queued", orb: "", sub: "Asked at the next calm moment." };
    else if (a?.cooldown_s) brain = { label: `Observing · ${a.cooldown_s}s`, orb: "", sub: "Letting you fly before the next question." };
    else if (a?.ready) brain = { label: "Ready to ask", orb: "thinking", sub: "Something worth learning just happened." };
    else brain = { label: "Observing", orb: "", sub: "Watching telemetry, camera and the 3-second forecast." };
  }
  const score = attention?.score ?? 0;

  const rowClass = (r: TranscriptRow) => `msg ${r.role}${r.high ? " high" : ""}`;

  return (
    <div className="ai-panel" style={hidden ? { display: "none" } : undefined}>
      {mode === "expert" ? (
        <>
          <div className="panel cortex">
            <div className="panel-head">
              <span className="eyebrow">Apprentice cortex</span>
              <button className="btn btn-sm" onClick={triggerQuestion} disabled={!sessionActive}>
                Ask now
              </button>
            </div>
            <div className="brain">
              <div className={`brain-orb ${brain.orb}`} />
              <div className="brain-state">
                <strong>{brain.label}</strong>
                <span className="dim" style={{ fontSize: 12 }}>{brain.sub}</span>
                <div className="attn-meter">
                  <div className={`attn-fill ${attention?.ready ? "ready" : ""}`} style={{ width: `${Math.min(100, (score / 6) * 100)}%` }} />
                  <div className="attn-threshold" style={{ left: "50%" }} />
                </div>
                <div className="attn-meta">
                  <span>ATTENTION {score.toFixed(1)}</span>
                  <span>ASK ≥ 3.0</span>
                </div>
              </div>
            </div>
            {sessionActive && attention?.reasons?.length ? (
              <ul className="why">
                {attention.reasons.slice(0, 3).map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            ) : null}
            {sessionActive && attention?.targets?.length ? (
              <div className="target-chips">
                {attention.targets.map((t) => (
                  <span key={t.slot} className={`tchip kind-${t.kind}`}>
                    <b>{t.kind === "hypothesis" ? "HABIT" : t.kind === "deviation" ? "CHECK" : "LEARN"}</b>
                    {t.name}
                  </span>
                ))}
              </div>
            ) : null}
            {latestObservation && sessionActive && <div className="observation">👁 {latestObservation.observation}</div>}
          </div>

          <div className="panel question-card">
            <div className="q-meta">
              <span className="eyebrow">Question</span>
              {currentQuestion && <span className={`q-kind ${currentMeta.kind ?? "rule"}`}>{KIND_LABEL[currentMeta.kind ?? "rule"]}</span>}
              {currentQuestion && currentMeta.slotName && (
                <span className="q-slot">
                  learning <b>{currentMeta.slotName}</b>
                </span>
              )}
            </div>
            {currentQuestion ? (
              <p className="q-text">
                {typed}
                {typed.length < currentQuestion.length && <span className="caret" />}
              </p>
            ) : (
              <p className="q-text placeholder">
                The apprentice asks when something worth learning happens: a road crossing, a defect, a near miss, a habit it noticed.
              </p>
            )}
            {currentQuestion && (
              <div className="q-actions">
                <button className="btn btn-sm" onClick={() => speakText(currentQuestion)} disabled={listening}>
                  ▶ Replay
                </button>
              </div>
            )}
          </div>
        </>
      ) : (
        <>
          <div className={`panel tutor-card urgency-${latestAdvice?.urgency ?? "low"}`}>
            <div className="panel-head">
              <span className="eyebrow">AI flight tutor</span>
              {latestAdvice && <span className={`chip ${latestAdvice.urgency === "high" ? "bad" : latestAdvice.urgency === "medium" ? "warn" : "ok"}`}>{latestAdvice.category.replace("_", " ")}</span>}
            </div>
            <p className="advice">{latestAdvice?.speech ?? "The tutor coaches you with the rules the expert taught, and warns you before a danger."}</p>
            {latestAdvice?.knowledge_reference && <div className="ref">◆ {latestAdvice.knowledge_reference}</div>}
            {latestAdvice && (
              <div className="q-actions">
                <button className="btn btn-sm" onClick={() => speakText(latestAdvice.speech)}>
                  ▶ Replay
                </button>
              </div>
            )}
          </div>
          <div className="panel guardian-card">
            <div className={`shield ${guardian?.engaged ? "engaged" : guardian?.enabled ? "" : "off"}`}>
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#a780ff" strokeWidth="2">
                <path d="M12 2 4 5v6c0 5 3.5 9.5 8 11 4.5-1.5 8-6 8-11V5z" />
                <path d="m8.5 12 2.5 2.5 4.5-5" />
              </svg>
            </div>
            <div className="g-text">
              <strong>Guardian {guardian?.enabled ? (guardian.engaged ? "engaged" : "armed") : "off"}</strong>
              Model-predictive collision avoidance: it takes the sticks for a moment when a crash is predicted.
              {" "}
              <span className="mono">{guardian?.interventions ?? 0} interventions</span>
            </div>
          </div>
        </>
      )}

      <div className="panel voice-line">
        <span className={`voice-label ${vizMode}`}>{voiceLabel}</span>
        <VoiceViz mode={vizMode} getLevel={voiceLevel} />
      </div>

      <div className="panel">
        <div className="mic-controls">
          {listening ? (
            <>
              <span className="rec-indicator">
                ● {recording === "note" ? "Recording your note" : recording === "question" ? "Listening to your question" : "Recording your answer"}
              </span>
              <button className="btn btn-primary btn-sm" onClick={() => recordingRef.current?.finish()}>
                ⏹ {recording === "question" ? "Ask" : "Save"}
              </button>
              <button className="btn btn-sm" onClick={() => recordingRef.current?.cancel()}>
                Discard
              </button>
            </>
          ) : mode === "expert" ? (
            <button className="btn btn-record" onClick={recordNote} disabled={!sessionActive || isSubmitting}>
              🎙 Record a note <kbd>R</kbd>
            </button>
          ) : (
            <button className="btn btn-record" onClick={askTutorByVoice} disabled={!sessionActive || isSubmitting}>
              {isSubmitting ? "⏳ Tutor is thinking…" : "🎙 Ask the tutor"} <kbd>R</kbd>
            </button>
          )}
        </div>
        {mode === "expert" ? (
          <div className="composer">
            <input
              className="field"
              type="text"
              placeholder={currentQuestion ? "Speak after the beep, or type your answer…" : "Type a note for the apprentice…"}
              value={answerInput}
              onChange={(e) => setAnswerInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitAnswer()}
            />
            <button className="btn btn-primary" onClick={() => submitAnswer()} disabled={isSubmitting || !answerInput.trim()}>
              {isSubmitting ? "…" : "Teach"}
            </button>
          </div>
        ) : (
          <div className="composer">
            <input
              className="field"
              type="text"
              placeholder="Ask the tutor (e.g. how high should I cross the road?)"
              value={noviceQueryInput}
              onChange={(e) => setNoviceQueryInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && askTutor()}
            />
            <button className="btn btn-primary" onClick={() => askTutor()} disabled={!noviceQueryInput.trim()}>
              Ask
            </button>
          </div>
        )}
      </div>

      {mode === "expert" && lastSavedInsight && (
        <div className="panel insight-card" key={lastSavedInsight.text}>
          <span className="eyebrow">Rule acquired{lastSavedInsight.slot ? ` · ${lastSavedInsight.slot}` : ""}</span>
          <p>{lastSavedInsight.text}</p>
        </div>
      )}

      <div className="panel">
        <div className="panel-head">
          <span className="eyebrow">Comms log</span>
          <span className="dim mono" style={{ fontSize: 11 }}>{transcript.length} msgs</span>
        </div>
        <div className="transcript" ref={logRef}>
          {transcript.length === 0 ? (
            <div className="empty-note">No conversation yet.</div>
          ) : (
            transcript.map((row, i) => (
              <div key={i} className={rowClass(row)}>
                <div className="msg-meta">
                  <span>{ROLE_LABEL[row.role]}</span>
                  {row.tag && <span>· {row.tag}</span>}
                  {row.t !== undefined && <span className="t">{row.t.toFixed(1)}s</span>}
                </div>
                <div>{row.text}</div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
