'use client';

/**
 * session.ts — the WebRTC RTCPeerConnection lifecycle for the realtime voice host.
 *
 * Browser-only (uses RTCPeerConnection + getUserMedia). It connects to OpenAI's
 * realtime calls endpoint using ONLY the ephemeral secret minted by our server, wires
 * the mic up + the model's voice down, and on each model tool call forwards it to the
 * SERVER (via routeToolCall) — feeding the JSON result back as a function_call_output.
 * It never sends/publishes; the only two tools exist server-side behind the GO-gate.
 */

import {
  mintVoiceSession,
  routeToolCall,
  type MintedVoiceSession,
  type OrchestrateResult,
  type PlanUpdateResult,
} from './realtime';

export interface VoiceSessionCallbacks {
  onStatus?: (status: VoiceConnState) => void;
  onUserTranscript?: (text: string) => void;
  onAssistantTranscript?: (text: string, done: boolean) => void;
  onPlan?: (r: PlanUpdateResult) => void;
  onOrchestrate?: (r: OrchestrateResult) => void;
  onError?: (msg: string) => void;
  /** The REAL local mic MediaStream once getUserMedia resolves — for the orb's
   *  input AnalyserNode. Honest by construction: it reacts to your actual voice. */
  onMicStream?: (stream: MediaStream) => void;
  /** The REAL remote model-TTS MediaStream once the track arrives — for the orb's
   *  output AnalyserNode (the orb pulses to what the host is actually saying). */
  onRemoteStream?: (stream: MediaStream) => void;
}

export type VoiceConnState = 'idle' | 'minting' | 'connecting' | 'live' | 'closed' | 'error';

export class RealtimeVoiceSession {
  private pc: RTCPeerConnection | null = null;
  private dc: RTCDataChannel | null = null;
  private mic: MediaStream | null = null;
  private audioEl: HTMLAudioElement | null = null;
  private lastUserTranscript = '';

  constructor(
    private readonly aguiUrl: string,
    private readonly sessionId: string,
    private readonly cb: VoiceSessionCallbacks = {},
  ) {}

  /** The latest finalized user-speech transcript (drives the GO-gate go-phrase factor). */
  latestTranscript = (): string => this.lastUserTranscript;

  async connect(): Promise<void> {
    try {
      this.cb.onStatus?.('minting');
      const minted: MintedVoiceSession = await mintVoiceSession(this.aguiUrl, this.sessionId);

      this.cb.onStatus?.('connecting');
      const pc = new RTCPeerConnection();
      this.pc = pc;

      // Model voice down: attach the remote track to a hidden <audio> element.
      const audioEl = document.createElement('audio');
      audioEl.autoplay = true;
      this.audioEl = audioEl;
      pc.ontrack = (e) => {
        audioEl.srcObject = e.streams[0];
        // Surface the real model-TTS stream so the orb can analyse the host's voice.
        if (e.streams[0]) this.cb.onRemoteStream?.(e.streams[0]);
      };

      // Mic up.
      this.mic = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.cb.onMicStream?.(this.mic);
      for (const track of this.mic.getTracks()) pc.addTrack(track, this.mic);

      // Events both ways on the data channel.
      const dc = pc.createDataChannel('oai-events');
      this.dc = dc;
      dc.onmessage = (e) => this.onEvent(e.data);
      dc.onopen = () => this.cb.onStatus?.('live');

      // SDP offer/answer against OpenAI, authenticated with the EPHEMERAL secret only.
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const resp = await fetch(`${minted.callUrl}?model=${encodeURIComponent(minted.model)}`, {
        method: 'POST',
        body: offer.sdp,
        headers: {
          Authorization: `Bearer ${minted.value}`,
          'Content-Type': 'application/sdp',
        },
      });
      if (!resp.ok) throw new Error(`realtime SDP exchange HTTP ${resp.status}`);
      const answer = await resp.text();
      await pc.setRemoteDescription({ type: 'answer', sdp: answer });
    } catch (err) {
      this.cb.onStatus?.('error');
      this.cb.onError?.(err instanceof Error ? err.message : 'voice connect failed');
      this.close();
    }
  }

  /** Ask the model to speak a short narration line (used for run-summary narration). */
  narrate(text: string): void {
    if (!this.dc || this.dc.readyState !== 'open') return;
    this.dc.send(
      JSON.stringify({ type: 'response.create', response: { instructions: text } }),
    );
  }

  close(): void {
    try {
      this.dc?.close();
    } catch {
      /* ignore */
    }
    try {
      this.pc?.close();
    } catch {
      /* ignore */
    }
    for (const t of this.mic?.getTracks() ?? []) {
      try {
        t.stop();
      } catch {
        /* ignore */
      }
    }
    if (this.audioEl) this.audioEl.srcObject = null;
    this.pc = null;
    this.dc = null;
    this.mic = null;
    this.cb.onStatus?.('closed');
  }

  // --- realtime event handling --------------------------------------------- #

  private async onEvent(raw: unknown): Promise<void> {
    let evt: Record<string, unknown>;
    try {
      evt = JSON.parse(typeof raw === 'string' ? raw : String(raw)) as Record<string, unknown>;
    } catch {
      return;
    }
    const type = String(evt.type ?? '');

    // Finalized USER speech transcript — the go-phrase factor of the GO-gate.
    if (type === 'conversation.item.input_audio_transcription.completed') {
      const t = String((evt.transcript as string) ?? '').trim();
      if (t) {
        this.lastUserTranscript = t;
        this.cb.onUserTranscript?.(t);
      }
      return;
    }

    // ASSISTANT spoken transcript (for the on-screen caption).
    if (type.endsWith('audio_transcript.delta')) {
      this.cb.onAssistantTranscript?.(String((evt.delta as string) ?? ''), false);
      return;
    }
    if (type.endsWith('audio_transcript.done')) {
      this.cb.onAssistantTranscript?.(String((evt.transcript as string) ?? ''), true);
      return;
    }

    // MODEL TOOL CALL — forward to the SERVER handler; the GO-gate lives there.
    const fnDone =
      type === 'response.function_call_arguments.done' ||
      (type === 'response.output_item.done' &&
        (evt.item as { type?: string } | undefined)?.type === 'function_call');
    if (fnDone) {
      const item = (evt.item as Record<string, unknown> | undefined) ?? evt;
      const name = String(item.name ?? evt.name ?? '');
      const callId = String(item.call_id ?? evt.call_id ?? '');
      const args = String(item.arguments ?? evt.arguments ?? '');
      if (!name) return;
      const output = await routeToolCall(name, args, {
        aguiUrl: this.aguiUrl,
        sessionId: this.sessionId,
        latestTranscript: this.latestTranscript,
        onPlan: this.cb.onPlan,
        onOrchestrate: this.cb.onOrchestrate,
      });
      this.sendToolOutput(callId, output);
      return;
    }

    if (type === 'error') {
      const err = (evt.error as { message?: string } | undefined)?.message ?? 'realtime error';
      this.cb.onError?.(err);
    }
  }

  private sendToolOutput(callId: string, output: Record<string, unknown>): void {
    if (!this.dc || this.dc.readyState !== 'open') return;
    this.dc.send(
      JSON.stringify({
        type: 'conversation.item.create',
        item: { type: 'function_call_output', call_id: callId, output: JSON.stringify(output) },
      }),
    );
    // Let the model continue (narrate the result / keep interviewing).
    this.dc.send(JSON.stringify({ type: 'response.create' }));
  }
}
