import { useEffect, useRef, useState } from "react";
import { AIAdvice, AIObservation, AIQuestion } from "./useSimSocket";
import { API_URL } from "./config";
import { AnswerRecording, beep, recordAnswer } from "./recordAnswer";

type TranscriptRow = { role: string; text: string; t?: number; tag?: string };

// Voice loop (expert mode): Claude's question is spoken with ElevenLabs text-to-speech, then the mic
// opens for one answer only, closes on silence, and the audio is transcribed by ElevenLabs on the backend.
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
  const [lastSavedInsight, setLastSavedInsight] = useState<string | null>(null);
  const [audioPlaying, setAudioPlaying] = useState<boolean>(false);
  const [listening, setListening] = useState<boolean>(false);

  const lastQuestionRef = useRef<string>("");
  const lastAdviceRef = useRef<string>("");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const speechGenRef = useRef(0); // bumped to cancel speech that is still being fetched
  const playbackEndRef = useRef<((finished: boolean) => void) | null>(null);
  const recordingRef = useRef<AnswerRecording | null>(null);
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
  const showAnswerResult = (question: string, answer: string, data: { insight?: string | null; rejected?: boolean }) => {
    addRow({ role: "expert_operator", text: answer, t: simTime, tag: "Answer" });
    if (data.rejected) {
      addRow({ role: "system", text: "Not saved: no usable know-how in that answer." });
    } else if (data.insight) {
      setLastSavedInsight(data.insight);
      onKnowledgeUpdated?.();
    }
    setCurrentQuestion((q) => (q === question ? "" : q)); // answered; wait for the next one
  };

  const skipQuestion = async (question: string, reason: string) => {
    await fetch(`${API_URL}/dialogue/skip`, { method: "POST" }).catch(() => {});
    setCurrentQuestion((q) => (q === question ? "" : q));
    addRow({ role: "system", text: reason });
  };

  /** Mic on for one answer, then off. Enter = done, Esc = skip, silence = done. */
  const listenForAnswer = async (question: string) => {
    let recording: AnswerRecording;
    try {
      beep();
      await new Promise((r) => setTimeout(r, 250)); // don't record the beep
      recording = await recordAnswer();
    } catch (err) {
      addRow({ role: "system", text: `Microphone unavailable (${(err as Error)?.message ?? err}). Type your answer.` });
      return;
    }
    recordingRef.current = recording;
    setListening(true);
    const audio = await recording.done;
    setListening(false);
    if (recordingRef.current === recording) recordingRef.current = null;

    // flight ended, a new question arrived, or the answer was typed meanwhile
    if (!liveRef.current.sessionActive || liveRef.current.currentQuestion !== question) return;
    if (!audio) {
      await skipQuestion(question, "No answer recorded: question skipped.");
      return;
    }

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
        addRow({ role: "system", text: "Nothing understood in the recording: question skipped." });
        setCurrentQuestion((q) => (q === question ? "" : q));
      } else {
        showAnswerResult(question, data.transcript, data);
      }
    } catch (err) {
      addRow({ role: "system", text: `Could not process the spoken answer: ${(err as Error)?.message ?? err}` });
    } finally {
      setIsSubmitting(false);
    }
  };

  /** New question: show it, speak it, then listen for the answer. */
  const askAndListen = async (question: string, t?: number) => {
    recordingRef.current?.cancel();
    lastQuestionRef.current = question;
    liveRef.current.currentQuestion = question;
    setCurrentQuestion(question);
    addRow({ role: "apprentice_model", text: question, t, tag: "Question" });
    const spoken = await speakText(question);
    if (spoken && mode === "expert" && liveRef.current.sessionActive && liveRef.current.currentQuestion === question) {
      await listenForAnswer(question);
    }
  };

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
      if (sessionActive) askAndListen(latestQuestion.question, latestQuestion.t);
    }
  }, [latestQuestion]);

  // Tutor advice (novice mode): spoken only
  useEffect(() => {
    if (latestAdvice?.speech && latestAdvice.speech !== lastAdviceRef.current) {
      lastAdviceRef.current = latestAdvice.speech;
      if (!sessionActive) return;
      addRow({ role: "tutor_model", text: latestAdvice.speech, t: simTime, tag: latestAdvice.category.toUpperCase() });
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
      if (data.question && liveRef.current.sessionActive) askAndListen(data.question, data.t);
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
        body: JSON.stringify({ session_id: sessionId, question: question || "General inspection insight", answer: ans }),
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
    addRow({ role: "novice_operator", text: query, t: simTime, tag: "Question" });

    try {
      const res = await fetch(`${API_URL}/dialogue/advise`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, query }),
      });
      const advice: AIAdvice = await res.json();
      if (advice.speech) {
        addRow({ role: "tutor_model", text: advice.speech, t: simTime, tag: "TUTOR ANSWER" });
        speakText(advice.speech);
      }
    } catch (err) {
      console.error("Failed to ask tutor:", err);
    }
  };

  const voiceStatus = listening
    ? "🎙 Listening… Enter = done · Esc = skip"
    : isSubmitting
      ? "⏳ Processing answer…"
      : audioPlaying
        ? "🔊 Speaking"
        : sessionActive
          ? "Ready: questions are spoken, mic opens after each one"
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
            <p>{currentQuestion || "Fly the drone to trigger questions or click 'Ask Question Now'."}</p>
            {currentQuestion && (
              <button className="btn-icon" onClick={() => speakText(currentQuestion)} disabled={listening}>
                🔊 Replay Voice
              </button>
            )}
          </div>

          <div className="answer-input-group">
            <input
              type="text"
              placeholder="Speak after the beep, or type your answer here..."
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
              <strong>Knowledge Acquired:</strong>
              <div>{lastSavedInsight}</div>
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

