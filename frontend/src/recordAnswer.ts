// Microphone for spoken answers (opened after a question has been read) and for the pilot's own
// notes (opened with Record a note). Closed automatically on silence, or with Stop / Discard.

const SPEECH_LEVEL = 0.02; // RMS above this counts as talking

export type RecordingLimits = {
  silenceMs: number; // stop this long after the pilot stops talking
  noSpeechMs: number; // give up if nothing is said
  maxMs: number; // hard limit
};
export const ANSWER_LIMITS: RecordingLimits = { silenceMs: 2000, noSpeechMs: 8000, maxMs: 30000 };
// A note has no question to frame it: the pilot pauses to think, so wait longer
export const NOTE_LIMITS: RecordingLimits = { silenceMs: 4000, noSpeechMs: 10000, maxMs: 60000 };

export type AnswerRecording = {
  done: Promise<Blob | null>; // null = nothing said, skipped or cancelled
  finish: () => void; // Enter / Stop: stop now and keep the recording
  cancel: () => void; // Esc / Discard / end of flight: stop now and throw it away
  level: () => number; // current microphone loudness, 0..1, for the voice visualiser
};

/** Ask for microphone permission once (on Start Flight), then release the mic straight away. */
export async function primeMicrophone(): Promise<boolean> {
  audioContext(); // unlock audio while we have a user gesture
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    return true;
  } catch {
    return false;
  }
}

let sharedCtx: AudioContext | null = null;

/** One audio context for the voice visualiser, created during a click (Start Flight) so it can run. */
export function audioContext(): AudioContext | null {
  try {
    sharedCtx = sharedCtx ?? new AudioContext();
    if (sharedCtx.state === "suspended") void sharedCtx.resume();
    return sharedCtx;
  } catch {
    return null;
  }
}

/** Short tone so the pilot knows the mic is open. */
export function beep(): void {
  const ctx = new AudioContext();
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.frequency.value = 880;
  gain.gain.value = 0.15;
  osc.connect(gain).connect(ctx.destination);
  osc.start();
  osc.stop(ctx.currentTime + 0.15);
  osc.onended = () => ctx.close();
}

export async function recordAnswer(limits: RecordingLimits = ANSWER_LIMITS): Promise<AnswerRecording> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const recorder = new MediaRecorder(stream);
  const chunks: Blob[] = [];
  recorder.ondataavailable = (e) => {
    if (e.data.size) chunks.push(e.data);
  };

  const ctx = new AudioContext();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  ctx.createMediaStreamSource(stream).connect(analyser);
  const samples = new Float32Array(analyser.fftSize);

  const started = Date.now();
  let lastSpeech = started;
  let heardSpeech = false;
  let cancelled = false;
  let lastLevel = 0;
  let timer: ReturnType<typeof setInterval> | undefined;

  const done = new Promise<Blob | null>((resolve) => {
    recorder.onstop = () => {
      clearInterval(timer);
      stream.getTracks().forEach((t) => t.stop()); // mic off
      ctx.close();
      resolve(cancelled || !heardSpeech ? null : new Blob(chunks, { type: recorder.mimeType }));
    };
  });
  const stop = () => {
    if (recorder.state !== "inactive") recorder.stop();
  };

  timer = setInterval(() => {
    analyser.getFloatTimeDomainData(samples);
    const rms = Math.sqrt(samples.reduce((sum, v) => sum + v * v, 0) / samples.length);
    lastLevel = Math.min(1, rms * 8);
    const now = Date.now();
    if (rms > SPEECH_LEVEL) {
      heardSpeech = true;
      lastSpeech = now;
    }
    const silentTooLong = heardSpeech ? now - lastSpeech > limits.silenceMs : now - started > limits.noSpeechMs;
    if (silentTooLong || now - started > limits.maxMs) stop();
  }, 100);

  recorder.start();
  return {
    done,
    finish: stop,
    cancel: () => {
      cancelled = true;
      stop();
    },
    level: () => lastLevel,
  };
}
