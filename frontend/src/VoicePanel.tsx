import { useEffect, useMemo, useRef, useState } from "react";
import { useConversation } from "@elevenlabs/react";
import { AIAdvice, AIObservation, AIQuestion } from "./useSimSocket";

type TranscriptRow = { role: string; text: string; t?: number; tag?: string };

export function VoicePanel(props: {
  mode: "expert" | "novice";
  sessionId: string | null;
  simTime: number;
  events: Array<{ type: string; [k: string]: unknown }>;
  latestQuestion: AIQuestion | null;
  latestAdvice: AIAdvice | null;
  latestObservation?: AIObservation | null;
  onKnowledgeUpdated?: () => void;
}) {
  const {
    mode,
    sessionId,
    simTime,
    events,
    latestQuestion,
    latestAdvice,
    latestObservation,
    onKnowledgeUpdated,
  } = props;

  const agentId =
    mode === "novice"
      ? import.meta.env.VITE_ELEVENLABS_TUTOR_AGENT_ID || import.meta.env.VITE_ELEVENLABS_AGENT_ID
      : import.meta.env.VITE_ELEVENLABS_AGENT_ID;

  const [transcript, setTranscript] = useState<TranscriptRow[]>([]);
  const [elevenStatus, setElevenStatus] = useState<string>("disconnected");
  const [currentQuestion, setCurrentQuestion] = useState<string>("");
  const [answerInput, setAnswerInput] = useState<string>("");
  const [noviceQueryInput, setNoviceQueryInput] = useState<string>("");
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [lastSavedInsight, setLastSavedInsight] = useState<string | null>(null);
  const [audioPlaying, setAudioPlaying] = useState<boolean>(false);

  const lastForwardRef = useRef(0);
  const lastQuestionRef = useRef<string>("");
  const lastAdviceRef = useRef<string>("");

  // ElevenLabs Conversational AI hook
  const conversation: any = useConversation({
    onMessage: async (m: any) => {
      const role = m?.source === "user" ? "operator" : "elevenlabs_ai";
      const text = m?.message ?? "";
      if (!text) return;
      const row = { role, text, t: simTime };
      setTranscript((prev) => [...prev.slice(-40), row]);

      if (sessionId) {
        await fetch(`http://localhost:8000/session/${sessionId}/transcript`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role, text, t: simTime }),
        }).catch(() => {});
      }

      // If in expert mode and operator spoke an answer, trigger knowledge distillation
      if (mode === "expert" && role === "operator" && currentQuestion) {
        submitAnswer(text);
      }
    },
    onStatusChange: (s: string) => setElevenStatus(s),
  });

  // Start ElevenLabs Conversational Session
  const toggleVoiceSession = async () => {
    if (elevenStatus === "connected") {
      conversation.endSession?.();
      setElevenStatus("disconnected");
      return;
    }

    if (!agentId) {
      alert(
        "VITE_ELEVENLABS_AGENT_ID is not configured in .env. You can still type responses or use ElevenLabs TTS audio playback below!"
      );
      return;
    }

    try {
      await navigator.mediaDevices.getUserMedia({ audio: true });
      await conversation.startSession({ agentId });
      const rolePrompt =
        mode === "expert"
          ? "You are an apprentice AI learning from a senior drone pilot inspecting power lines. Ask concise questions when events occur."
          : "You are an AI Flight Tutor coaching a novice drone pilot on power lines using knowledge.md rules. Give safety-first spoken advice.";
      await conversation.sendContextualUpdate?.(rolePrompt);
    } catch (err) {
      console.error("Failed to start ElevenLabs session:", err);
      alert("Could not access microphone or connect to ElevenLabs agent.");
    }
  };

  // Play audio via ElevenLabs TTS or browser fallback
  const speakText = async (text: string) => {
    if (!text) return;
    setAudioPlaying(true);
    try {
      const res = await fetch("http://localhost:8000/elevenlabs/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.onended = () => setAudioPlaying(false);
        audio.onerror = () => setAudioPlaying(false);
        await audio.play();
        return;
      }
    } catch {
      // Fallback to browser SpeechSynthesis
    }

    if ("speechSynthesis" in window) {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1.0;
      utterance.onend = () => setAudioPlaying(false);
      utterance.onerror = () => setAudioPlaying(false);
      window.speechSynthesis.speak(utterance);
    } else {
      setAudioPlaying(false);
    }
  };

  // Synchronize incoming question from backend
  useEffect(() => {
    if (latestQuestion?.question && latestQuestion.question !== lastQuestionRef.current) {
      lastQuestionRef.current = latestQuestion.question;
      setCurrentQuestion(latestQuestion.question);
      setTranscript((prev) => [
        ...prev.slice(-40),
        { role: "apprentice_model", text: latestQuestion.question, t: latestQuestion.t, tag: "Question" },
      ]);
      speakText(latestQuestion.question);
      if (conversation?.sendContextualUpdate && elevenStatus === "connected") {
        conversation.sendContextualUpdate(`[AI QUESTION] ${latestQuestion.question}`);
      }
    }
  }, [latestQuestion]);

  // Synchronize incoming advice from backend
  useEffect(() => {
    if (latestAdvice?.speech && latestAdvice.speech !== lastAdviceRef.current) {
      lastAdviceRef.current = latestAdvice.speech;
      setTranscript((prev) => [
        ...prev.slice(-40),
        {
          role: "tutor_model",
          text: latestAdvice.speech,
          t: simTime,
          tag: latestAdvice.category.toUpperCase(),
        },
      ]);
      speakText(latestAdvice.speech);
      if (conversation?.sendContextualUpdate && elevenStatus === "connected") {
        conversation.sendContextualUpdate(`[AI ADVICE] ${latestAdvice.speech}`);
      }
    }
  }, [latestAdvice]);

  // Push critical flight events to ElevenLabs conversation
  useEffect(() => {
    const ev = events[events.length - 1];
    if (!ev) return;
    const now = Date.now();
    if (now - lastForwardRef.current < 2500) return;
    lastForwardRef.current = now;

    if (conversation?.sendContextualUpdate && elevenStatus === "connected") {
      conversation.sendContextualUpdate(
        `[DRONE EVENT at t=${simTime.toFixed(1)}s] ${ev.type}`
      );
    }
  }, [events, simTime, conversation, elevenStatus]);

  // Expert Mode: Trigger manual question from AI
  const triggerQuestion = async () => {
    try {
      const res = await fetch("http://localhost:8000/dialogue/trigger-question", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: "manual_inquiry", t: simTime }),
      });
      const data = await res.json();
      if (!res.ok) {
        alert(data.detail ?? "Could not get a question from the apprentice.");
        return;
      }
      if (data.question) {
        lastQuestionRef.current = data.question;
        setCurrentQuestion(data.question);
        setTranscript((prev) => [
          ...prev.slice(-40),
          { role: "apprentice_model", text: data.question, t: data.t, tag: "Question" },
        ]);
        speakText(data.question);
      }
    } catch (err) {
      console.error("Failed to trigger question:", err);
    }
  };

  // Expert Mode: Submit Answer & update knowledge.md
  const submitAnswer = async (textToSubmit?: string) => {
    const ans = textToSubmit || answerInput;
    if (!ans.trim()) return;

    setIsSubmitting(true);
    try {
      const res = await fetch("http://localhost:8000/dialogue/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          question: currentQuestion || "General inspection insight",
          answer: ans,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setLastSavedInsight(data.insight);
        setAnswerInput("");
        setTranscript((prev) => [
          ...prev.slice(-40),
          { role: "expert_operator", text: ans, t: simTime, tag: "Answer" },
        ]);
        onKnowledgeUpdated?.();
      }
    } catch (err) {
      console.error("Failed to submit answer:", err);
    } finally {
      setIsSubmitting(false);
    }
  };

  // Novice Mode: Ask question to AI Tutor
  const askTutor = async () => {
    if (!noviceQueryInput.trim()) return;
    const query = noviceQueryInput;
    setNoviceQueryInput("");
    setTranscript((prev) => [
      ...prev.slice(-40),
      { role: "novice_operator", text: query, t: simTime, tag: "Question" },
    ]);

    try {
      const res = await fetch("http://localhost:8000/dialogue/advise", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, query }),
      });
      const advice: AIAdvice = await res.json();
      if (advice.speech) {
        setTranscript((prev) => [
          ...prev.slice(-40),
          { role: "tutor_model", text: advice.speech, t: simTime, tag: "TUTOR ANSWER" },
        ]);
        speakText(advice.speech);
      }
    } catch (err) {
      console.error("Failed to ask tutor:", err);
    }
  };

  return (
    <div className="voice-panel">
      {/* ElevenLabs Status Header */}
      <div className="voice-header">
        <div className="voice-status">
          <span className={`status-dot ${elevenStatus}`} />
          <strong>ElevenLabs Voice:</strong> {elevenStatus}
          {audioPlaying && <span className="audio-badge">🔊 Speaking</span>}
        </div>
        <button
          className={`btn-voice ${elevenStatus === "connected" ? "connected" : ""}`}
          onClick={toggleVoiceSession}
        >
          {elevenStatus === "connected" ? "End Voice Chat" : "Connect Voice (ElevenLabs)"}
        </button>
      </div>

      {/* Mode-Specific Interaction Area */}
      {mode === "expert" ? (
        <div className="interaction-box expert">
          <div className="section-title">
            <span>🎓 AI Apprentice Learner</span>
            <button className="btn-sm" onClick={triggerQuestion}>
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
              <button className="btn-icon" onClick={() => speakText(currentQuestion)}>
                🔊 Replay Voice
              </button>
            )}
          </div>

          <div className="answer-input-group">
            <input
              type="text"
              placeholder="Type or speak answer (e.g. 'I slowed down because the insulator showed flashover wear')..."
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
            <button className="btn-primary" onClick={askTutor} disabled={!noviceQueryInput.trim()}>
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

