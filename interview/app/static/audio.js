/**
 * Voice interview: getUserMedia → AudioWorklet (16 kHz PCM) → WS,
 * and 24 kHz PCM playback queue with immediate stop on barge-in.
 */
(function (global) {
  const TARGET_IN_RATE = 16000;
  const PLAY_RATE = 24000;

  const workletCode = `
class PcmDownsampleProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._ratio = sampleRate / ${TARGET_IN_RATE};
    this._acc = 0;
    this._buf = [];
  }
  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const ch = input[0];
    for (let i = 0; i < ch.length; i++) {
      this._acc += 1;
      if (this._acc >= this._ratio) {
        this._acc -= this._ratio;
        const s = Math.max(-1, Math.min(1, ch[i]));
        this._buf.push((s * 0x7fff) | 0);
        if (this._buf.length >= 1280) {
          const ab = new ArrayBuffer(this._buf.length * 2);
          const view = new Int16Array(ab);
          for (let j = 0; j < this._buf.length; j++) view[j] = this._buf[j];
          this.port.postMessage(ab, [ab]);
          this._buf = [];
        }
      }
    }
    return true;
  }
}
registerProcessor('pcm-downsample-processor', PcmDownsampleProcessor);
`;

  function b64FromArrayBuffer(ab) {
    const bytes = new Uint8Array(ab);
    let binary = "";
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
  }

  function arrayBufferFromB64(b64) {
    const binary = atob(b64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes.buffer;
  }

  class VoiceSession {
    constructor(options) {
      this.wsPath = options.wsPath;
      this.onEvent = options.onEvent || (() => {});
      this.ws = null;
      this.audioCtx = null;
      this.mediaStream = null;
      this.workletNode = null;
      this.sourceNode = null;
      this.muteGain = null;
      this.playing = false;
      this.playQueue = [];
      this.nextPlayTime = 0;
      this.activeSources = [];
      this.recording = false;
      this.ready = false;
    }

    _wsUrl() {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      return `${proto}//${location.host}${this.wsPath}`;
    }

    async start() {
      this.audioCtx = new AudioContext({ sampleRate: 48000 });
      if (this.audioCtx.state === "suspended") await this.audioCtx.resume();

      const blob = new Blob([workletCode], { type: "application/javascript" });
      const url = URL.createObjectURL(blob);
      await this.audioCtx.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);

      this.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          channelCount: 1,
        },
        video: false,
      });

      this.sourceNode = this.audioCtx.createMediaStreamSource(this.mediaStream);
      this.workletNode = new AudioWorkletNode(this.audioCtx, "pcm-downsample-processor");
      this.muteGain = this.audioCtx.createGain();
      this.muteGain.gain.value = 0;
      this.sourceNode.connect(this.workletNode);
      this.workletNode.connect(this.muteGain);
      this.muteGain.connect(this.audioCtx.destination);

      this.workletNode.port.onmessage = (ev) => {
        if (!this.recording || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        this.ws.send(JSON.stringify({ type: "audio", data: b64FromArrayBuffer(ev.data) }));
      };

      await new Promise((resolve, reject) => {
        this.ws = new WebSocket(this._wsUrl());
        this.ws.onopen = () => resolve();
        this.ws.onerror = () => reject(new Error("WebSocket接続に失敗しました"));
        this.ws.onmessage = (ev) => this._onMessage(ev);
        this.ws.onclose = () => {
          this.ready = false;
          this.onEvent({ type: "status", status: "closed" });
        };
      });
    }

    _onMessage(ev) {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === "status" && msg.status === "ready") {
        this.ready = true;
      }
      if (msg.type === "audio" && msg.data) {
        this._enqueuePlayback(arrayBufferFromB64(msg.data), msg.rate || PLAY_RATE);
      }
      if (msg.type === "interrupted") {
        this.stopPlayback();
      }
      this.onEvent(msg);
    }

    _enqueuePlayback(pcmAb, rate) {
      if (!this.audioCtx) return;
      const int16 = new Int16Array(pcmAb);
      if (!int16.length) return;
      const float32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 0x8000;
      const buffer = this.audioCtx.createBuffer(1, float32.length, rate);
      buffer.copyToChannel(float32, 0);
      const now = this.audioCtx.currentTime;
      if (this.nextPlayTime < now) this.nextPlayTime = now;
      const src = this.audioCtx.createBufferSource();
      src.buffer = buffer;
      src.connect(this.audioCtx.destination);
      src.start(this.nextPlayTime);
      this.nextPlayTime += buffer.duration;
      this.activeSources.push(src);
      src.onended = () => {
        this.activeSources = this.activeSources.filter((s) => s !== src);
      };
      this.playing = true;
    }

    stopPlayback() {
      for (const src of this.activeSources) {
        try {
          src.stop();
        } catch (_) {
          /* already stopped */
        }
      }
      this.activeSources = [];
      this.nextPlayTime = this.audioCtx ? this.audioCtx.currentTime : 0;
      this.playing = false;
      this.playQueue = [];
    }

    startMic() {
      this.recording = true;
      this.onEvent({ type: "mic", status: "on" });
    }

    stopMic() {
      this.recording = false;
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: "audio_end" }));
      }
      this.onEvent({ type: "mic", status: "off" });
    }

    sendText(text) {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      this.ws.send(JSON.stringify({ type: "text", text }));
    }

    endSession() {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: "end" }));
      }
    }

    async stop() {
      this.recording = false;
      this.stopPlayback();
      try {
        this.workletNode && this.workletNode.disconnect();
      } catch (_) {}
      try {
        this.sourceNode && this.sourceNode.disconnect();
      } catch (_) {}
      if (this.mediaStream) {
        this.mediaStream.getTracks().forEach((t) => t.stop());
        this.mediaStream = null;
      }
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.close();
      }
      if (this.audioCtx) {
        await this.audioCtx.close();
        this.audioCtx = null;
      }
      this.ready = false;
    }
  }

  global.VoiceSession = VoiceSession;
})(window);
