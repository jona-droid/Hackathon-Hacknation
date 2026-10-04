// ElevenAgents in the browser: one conversation with the interviewer (expert flight + debrief) or
// the tutor (novice flight). The agent words and speaks the questions, listens (Scribe realtime,
// turn-taking) and hands the answers back through client tools, forwarded here to the backend.
// The backend gives a signed URL per conversation: the ElevenLabs key never reaches the browser.

import { Conversation, VoiceConversation } from "@elevenlabs/client";
import { API_URL } from "./config";

export type AgentRole = "interviewer" | "tutor";
export type AgentStatus = "off" | "connecting" | "connected" | "disconnected" | "error";

export type AgentHandlers = {
  onAgentText: (text: string) => void;
  onUserText: (text: string) => void;
  onMode: (speaking: boolean) => void;
  onStatus: (status: AgentStatus) => void;
  tools: Record<string, (params: Record<string, unknown>) => Promise<string>>;
};

export const AGENT_TOOLS = ["save_answer", "skip_question", "save_note", "teachback_verdict", "log_prediction"];

/** Expressive Mode text carries audio tags such as [happy]: shown without them. */
export const stripTags = (text: string) => text.replace(/\[[^\]]{1,30}\]\s*/g, "").trim();

export class VoiceAgent {
  private conv: VoiceConversation | null = null;
  private starting: Promise<boolean> | null = null;
  role: AgentRole | null = null;
  speaking = false;

  constructor(private handlers: () => AgentHandlers) {}

  get connected(): boolean {
    return !!this.conv && this.conv.isOpen();
  }

  /** Connect (once per role); muted = the microphone starts closed (expert: opened per question). */
  start(role: AgentRole, muted: boolean): Promise<boolean> {
    if (this.connected && this.role === role) {
      this.mute(muted);
      return Promise.resolve(true);
    }
    if (this.starting) return this.starting;
    this.starting = this.open(role, muted).finally(() => {
      this.starting = null;
    });
    return this.starting;
  }

  private async open(role: AgentRole, muted: boolean): Promise<boolean> {
    await this.stop();
    const h = this.handlers;
    h().onStatus("connecting");
    try {
      const res = await fetch(`${API_URL}/agent/session?role=${role}`);
      if (!res.ok) throw new Error((await res.json()).detail ?? `HTTP ${res.status}`);
      const { signed_url, dynamic_variables } = await res.json();
      const clientTools = Object.fromEntries(
        AGENT_TOOLS.map((name) => [
          name,
          async (params: Record<string, unknown>) => {
            try {
              return (await h().tools[name]?.(params)) ?? "ok";
            } catch (err) {
              return `error: ${(err as Error)?.message ?? err}`;
            }
          },
        ])
      );
      const conv = (await Conversation.startSession({
        signedUrl: signed_url,
        connectionType: "websocket",
        dynamicVariables: dynamic_variables,
        clientTools,
        onMessage: ({ message, role: who }) => (who === "agent" ? h().onAgentText(message) : h().onUserText(message)),
        onModeChange: ({ mode }) => {
          this.speaking = mode === "speaking";
          h().onMode(this.speaking);
        },
        onDisconnect: () => {
          this.conv = null;
          this.role = null;
          this.speaking = false;
          h().onStatus("disconnected");
        },
        onError: (message: string) => console.warn("ElevenLabs agent:", message),
      })) as VoiceConversation;
      this.conv = conv;
      this.role = role;
      conv.setMicMuted(muted);
      h().onStatus("connected");
      return true;
    } catch (err) {
      console.warn("ElevenLabs agent unavailable:", err);
      h().onStatus("error");
      return false;
    }
  }

  async stop(): Promise<void> {
    const c = this.conv;
    this.conv = null;
    this.role = null;
    this.speaking = false;
    if (c) await c.endSession().catch(() => {});
  }

  /** A cue from the flight software: "[TAG] {json}". The agent follows it (see its prompt). */
  cue(tag: string, payload: unknown): void {
    this.conv?.sendUserMessage(`[${tag}] ${typeof payload === "string" ? payload : JSON.stringify(payload)}`);
  }

  /** Something the pilot typed instead of saying it. */
  say(text: string): void {
    this.conv?.sendUserMessage(text);
  }

  /** Background the agent knows about without answering it. */
  context(text: string): void {
    this.conv?.sendContextualUpdate(text);
  }

  mute(muted: boolean): void {
    this.conv?.setMicMuted(muted);
  }

  /** The pilot is busy on the sticks: resets the agent's turn timer, so it does not prompt them. */
  activity(): void {
    this.conv?.sendUserActivity();
  }

  volume(v: number): void {
    this.conv?.setVolume({ volume: v });
  }

  level(): number {
    if (!this.conv) return 0;
    try {
      return Math.min(1, (this.speaking ? this.conv.getOutputVolume() : this.conv.getInputVolume()) * 2);
    } catch {
      return 0;
    }
  }

  /** Resolves once the agent has finished speaking (at most maxMs). */
  async quiet(maxMs = 10000): Promise<void> {
    const start = Date.now();
    await new Promise((r) => setTimeout(r, 400));
    while (this.speaking && Date.now() - start < maxMs) await new Promise((r) => setTimeout(r, 200));
  }
}
