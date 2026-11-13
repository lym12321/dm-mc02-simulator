"use strict";

// One audio sink for the current session. QEMU timestamps preserve short tones.
class BuzzerAudio {
  constructor(report = () => {}) {
    this.report = report; this.nodes = new Set(); this.sequence = 0n;
    this.volume = .2; this.muted = false; this.running = false;
    this.latest = null; this.generation = null; this.offset = null;
  }

  async unlock() {
    try {
      if (!this.context) {
        this.context = new AudioContext();
        this.gain = this.context.createGain(); this.gain.connect(this.context.destination);
        this.gain.gain.value = this.muted ? 0 : this.volume;
      }
      await this.context.resume();
      this.report("");
      if (this.latest) this.update(...this.latest);
    } catch (error) { this.report(`声音未开启：${error.message}`); }
  }

  settings(volume, muted) {
    this.volume = Math.max(0, Math.min(1, volume)); this.muted = muted;
    if (this.gain) this.gain.gain.setTargetAtTime(muted ? 0 : this.volume, this.context.currentTime, .005);
  }

  silence() {
    for (const oscillator of this.nodes) {
      oscillator.onended = null; oscillator.stop(); oscillator.disconnect();
    }
    this.nodes.clear(); this.voice = null; this.offset = null; this.running = false;
  }

  tone(event, at) {
    if (this.voice) { this.voice.stop(at); this.voice = null; }
    const frequency = event.frequency_hz, duty = event.duty_permille / 1000;
    if (!(frequency > 0 && frequency < this.context.sampleRate / 2 && duty > 0 && duty < 1)) return;
    const harmonics = Math.min(64, Math.floor((this.context.sampleRate / 2 - 1) / frequency));
    const real = new Float32Array(harmonics + 1), imag = new Float32Array(harmonics + 1);
    for (let n = 1; n <= harmonics; n++) {
      real[n] = Math.sin(2 * Math.PI * n * duty) / (Math.PI * n);
      imag[n] = (1 - Math.cos(2 * Math.PI * n * duty)) / (Math.PI * n);
    }
    const oscillator = this.context.createOscillator();
    oscillator.frequency.value = frequency;
    oscillator.setPeriodicWave(this.context.createPeriodicWave(real, imag, {disableNormalization: true}));
    oscillator.connect(this.gain); oscillator.start(at);
    this.nodes.add(oscillator); this.voice = oscillator;
    oscillator.onended = () => { oscillator.disconnect(); this.nodes.delete(oscillator); };
  }

  update(state, connected) {
    this.latest = [state, connected];
    const source = state.board?.buzzer;
    const generation = source ? `${state.session_id}:${source.generation}` : null;
    if (generation !== this.generation) {
      this.silence(); this.sequence = 0n; this.generation = generation; this.failed = false;
    }
    const active = connected && state.status === "running" && !state.observation_stale;
    const events = source?.events || [];
    const last = events.length ? BigInt(events.at(-1).sequence) : this.sequence;
    if (!active || !source || this.context?.state !== "running") {
      this.silence(); this.sequence = last; return;
    }
    if (this.failed) return;
    const now = this.context.currentTime;
    const virtualNow = BigInt(state.board.timestamp_ns);
    let next = events.filter(event => BigInt(event.sequence) > this.sequence);
    if (this.sequence && next.length && BigInt(next[0].sequence) !== this.sequence + 1n) {
      this.silence(); this.failed = true;
      this.report("声音事件有缺口，已停止播放。重新启动会话可恢复。"); return;
    }
    if (!this.running) {
      // Initial connection may contain a short startup tone. Play at most the
      // recent 250 ms; paused observations have already advanced the cursor.
      if (this.sequence === 0n) next = next.filter(event => virtualNow - BigInt(event.timestamp_ns) <= 250000000n);
      if (!next.length) next = [{...source, timestamp_ns: virtualNow.toString()}];
      this.offset = now + .04;
      this.origin = BigInt(next[0].timestamp_ns);
      this.running = true;
    }
    if (next.length) {
      let firstTime = this.offset + Number(BigInt(next[0].timestamp_ns) - this.origin) / 1e9;
      if (firstTime < now || firstTime > now + .5) {
        // Reanchor the entire batch together: preserve the duration between its
        // start/stop events even after a slow native interval.
        this.offset += now + .04 - firstTime;
      }
      for (const event of next) this.tone(event, this.offset + Number(BigInt(event.timestamp_ns) - this.origin) / 1e9);
    }
    this.sequence = last;
  }
}
