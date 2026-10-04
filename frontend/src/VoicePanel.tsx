import { useEffect, useRef, useState } from "react";
import { AIAdvice, AIObservation, AIQuestion } from "./useSimSocket";
import { API_URL } from "./config";
import { AnswerRecording, NOTE_LIMITS, beep, recordAnswer } from "./recordAnswer";

type TranscriptRow = { role: string; text: string; t?: number; tag?: string };
type QuestionMeta = { slotName?: string | null; kind?: AIQuestion["kind"] };
type AnswerResult = {
  insight?: string | null;
  rejected?: boolean;
  slot_name?: string | null;
  follow_up?: string;
  error?: string;
};

const KIND_TAG: Record<string, string> = { deviation: "Rule check", follow_up: "Follow-up" };

// Voice loop (expert mode): Claude's question is spoken with ElevenLabs text-to-speech, then the mic
// opens for one answer only, closes on silence, and the audio is transcribed by ElevenLabs on the backend.
// The pilot can also record a note on their own (button or R): the apprentice holds its questions meanwhile.
export function VoicePanel(props: {
  mode: "expert" | "novice";
  sessionId: string | null;
  simTime: number;
  events: Array<{ type: string; [k: string]: unknown }>;
  latestQuestion: AIQuestion | null;
  latestAdvice: AIAdvice | null;
  latestObservation?: AIObservation | null;
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

  const lastQuestionRef = useRef<string>("");
  const lastAdviceRef = useRef<AIAdvice | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const speechGenRef = useRef(0); // bumped to cancel speech that is still being fetched
  const playbackEndRef = useRef<((finished: boolean) => void) | null>(null);
  const recordingRef = useRef<AnswerRecording | null>(null);
  const noteActiveRef = useRef(false); // from Record a note until the note is sent or discarded
  const wasActiveRef = useRef(sessionActive);
  // read by the async voice loop, which outlives the render it started in
  const liveRef = useRef({ sessionActive, currentQuestion });
  liveRef.current = { sessionActive, currentQuestion };

  const addRow = (row: TranscriptRow) => setTranscript((prev) => [...prev.slice(-40), row]);

  // ---- speaking (ElevenLabs text-to-speech, browser voice as fallback) ----
  const stopSpeaking = () => {
    speechGenRef.current += 1;
    audioRef.current?.pause();
    audioRef.current = null;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    playbackEndRef.current?.(false);
    playbackEndRef.current = null;
    setAudioPlaying(false);
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

  // ---- answers ----
  const showAnswerResult = (question: string, answer: string, data: AnswerResult) => {
    addRow({ role: "expert_operator", text: answer, t: simTime, tag: question ? "Answer" : "Note" });
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
      addRow({ role: "system", text: `Learned${data.slot_name ? ` (${data.slot_name})` : ""}: ${data.insight}` });
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
    addRow({ role: "novice_operator", text: query, t: simTime, tag: "Question" });
    if (advice.speech) {
      addRow({ role: "tutor_model", text: advice.speech, t: simTime, tag: "TUTOR ANSWER" });
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
    addRow({ role: "apprentice_model", text: question, t, tag: KIND_TAG[meta.kind ?? ""] ?? "Question" });
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
      addRow({ role: "tutor_model", text: latestAdvice.speech, t: simTime, tag: latestAdvice.category.toUpperCase() });
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
        alert(data.detail ?? "Could not get a question from the apprentice.");
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

  const voiceStatus =
    recording === "note"
      ? "🎙 Recording your note… Enter = save · Esc = discard"
      : recording === "answer"
        ? "🎙 Listening… Enter = done · Esc = skip"
        : recording === "question"
          ? "🎙 Listening to your question… Enter = send · Esc = cancel"
        : isSubmitting
          ? "⏳ Processing…"
          : audioPlaying
            ? "🔊 Speaking"
            : sessionActive
              ? mode === "expert"
                ? "Ready: mic opens after each question · R = record a note"
                : "Ready · R = ask the tutor by voice"
              : "Start a flight to begin";

  return (
    <div className="voice-panel" style={hidden ? { display: "none" } : undefined}>
      {/* Voice status: automatic, no connection step */}
      <div className="voice-header">
        <div className={`voice-status ${listening ? "listening" : ""}`}>
          <strong>Voice:</strong> {voiceStatus}
        </div>
      </div>

      {/* Mode-Specific Interaction Area */}
      {mode === "expert" ? (
        <div className="interaction-box expert">
          <div className="section-title">
            <span>🎓 AI Apprentice Learner</span>
            <button className="btn-sm" onClick={triggerQuestion} disabled={!sessionActive}>
              Ask Question Now
            </button>
          </div>

          {latestObservation && (
            <div className="observer-line">
              👁 {latestObservation.observation}
            </div>
          )}

          <div className="question-card">
            <strong>Current Question:</strong>
            {currentQuestion && currentMeta.slotName && (
              <div className="question-slot">
                {KIND_TAG[currentMeta.kind ?? ""] && <span className="badge">{KIND_TAG[currentMeta.kind ?? ""]}</span>}
                Learning: {currentMeta.slotName}
              </div>
            )}
            <p>{currentQuestion || "Fly the drone to trigger questions or click 'Ask Question Now'."}</p>
            {currentQuestion && (
              <button className="btn-icon" onClick={() => speakText(currentQuestion)} disabled={listening}>
                🔊 Replay Voice
              </button>
            )}
          </div>

          {/* Manual mic control: record a note on your own, or stop a recording that doesn't end */}
          <div className="record-controls">
            {listening ? (
              <>
                <span className="rec-indicator">● {recording === "note" ? "Recording your note" : "Recording your answer"}</span>
                <button className="btn-primary" onClick={() => recordingRef.current?.finish()}>
                  ⏹ Stop &amp; save
                </button>
                <button className="btn-sm" onClick={() => recordingRef.current?.cancel()}>
                  Discard
                </button>
              </>
            ) : (
              <button className="btn-record" onClick={recordNote} disabled={!sessionActive || isSubmitting}>
                🎙 Record a note <kbd>R</kbd>
              </button>
            )}
          </div>

          <div className="answer-input-group">
            <input
              type="text"
              placeholder={currentQuestion ? "Speak after the beep, or type your answer here..." : "Type a note for the apprentice..."}
              value={answerInput}
              onChange={(e) => setAnswerInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitAnswer()}
            />
            <button className="btn-primary" onClick={() => submitAnswer()} disabled={isSubmitting || !answerInput.trim()}>
              {isSubmitting ? "Distilling..." : "Submit to knowledge.md"}
            </button>
          </div>

          {lastSavedInsight && (
            <div className="insight-badge">
              <strong>Knowledge Acquired{lastSavedInsight.slot ? ` · ${lastSavedInsight.slot}` : ""}:</strong>
              <div>{lastSavedInsight.text}</div>
            </div>
          )}
        </div>
      ) : (
        <div className="interaction-box novice">
          <div className="section-title">
            <span>🛡️ AI Flight Tutor (Trained on knowledge.md)</span>
          </div>

          {latestAdvice ? (
            <div className={`advice-card ${latestAdvice.urgency}`}>
              <div className="advice-header">
                <span className="badge">{latestAdvice.category}</span>
                <span className="reference">{latestAdvice.knowledge_reference}</span>
              </div>
              <p className="advice-speech">"{latestAdvice.speech}"</p>
              <button className="btn-icon" onClick={() => speakText(latestAdvice.speech)}>
                🔊 Replay Advice
              </button>
            </div>
          ) : (
            <div className="advice-card neutral">
              <p>Operating within safety parameters. Approach cables or pylons to receive expert guidance.</p>
            </div>
          )}

          <div className="record-controls">
            {recording === "question" ? (
              <>
                <span className="rec-indicator">● Listening to your question</span>
                <button className="btn-primary" onClick={() => recordingRef.current?.finish()}>
                  ⏹ Stop &amp; ask
                </button>
                <button className="btn-sm" onClick={() => recordingRef.current?.cancel()}>
                  Cancel
                </button>
              </>
            ) : (
              <button className="btn-record" onClick={askTutorByVoice} disabled={listening || isSubmitting}>
                {isSubmitting ? "⏳ Tutor is thinking…" : "🎙 Ask the tutor by voice"} <kbd>R</kbd>
              </button>
            )}
          </div>

          <div className="query-input-group">
            <input
              type="text"
              placeholder="Ask Tutor (e.g. 'Why should I maintain 20m over the road?')..."
              value={noviceQueryInput}
              onChange={(e) => setNoviceQueryInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && askTutor()}
            />
            <button className="btn-primary" onClick={() => askTutor()} disabled={!noviceQueryInput.trim()}>
              Ask
            </button>
          </div>
        </div>
      )}

      {/* Transcript Log */}
      <div className="transcript-box">
        <div className="transcript-title">Flight Communication Transcript</div>
        <div className="transcript-list">
          {transcript.length === 0 ? (
            <div className="empty-state">No conversation yet. Speak or fly to trigger dialogue.</div>
          ) : (
            transcript.map((row, i) => (
              <div key={i} className={`transcript-row ${row.role}`}>
                <div className="row-meta">
                  <span className="role-label">{row.role}</span>
                  {row.tag && <span className="tag-label">{row.tag}</span>}
                  {row.t !== undefined && <span className="time-label">{row.t.toFixed(1)}s</span>}
                </div>
                <div className="row-text">{row.text}</div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

