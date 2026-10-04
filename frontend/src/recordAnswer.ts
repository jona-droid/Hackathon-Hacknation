// Microphone for spoken answers: opened only after a question has been read, closed automatically.

const SPEECH_LEVEL = 0.02; // RMS above this counts as talking
const SILENCE_MS = 2000; // stop this long after the pilot stops talking
const NO_SPEECH_MS = 8000; // give up if nothing is said
const MAX_MS = 30000; // hard limit for one answer

export type AnswerRecording = {
  done: Promise<Blob | null>; // null = nothing said, skipped or cancelled
  finish: () => void; // Enter: stop now and keep the answer
  cancel: () => void; // Esc / end of flight: stop now and discard
};

/** Ask for microphone permission once (on Start Flight), then release the mic straight away. */
export async function primeMicrophone(): Promise<boolean> {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    return true;
  } catch {
    return false;
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

export async function recordAnswer(): Promise<AnswerRecording> {
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
    const now = Date.now();
    if (rms > SPEECH_LEVEL) {
      heardSpeech = true;
      lastSpeech = now;
    }
    const silentTooLong = heardSpeech ? now - lastSpeech > SILENCE_MS : now - started > NO_SPEECH_MS;
    if (silentTooLong || now - started > MAX_MS) stop();
  }, 100);

  recorder.start();
  return {
    done,
    finish: stop,
    cancel: () => {
      cancelled = true;
      stop();
    },
  };
}
