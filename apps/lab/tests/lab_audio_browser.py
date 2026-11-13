#!/usr/bin/env python3
"""Render the actual Web Audio sink to PCM, including retained short events."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page()
            page.add_script_tag(path=ROOT / "src/buzzer-audio.js")
            result = page.evaluate("""async () => {
                async function render(volume, action) {
                    const errors = [];
                    const sink = new BuzzerAudio(message => { if (message) errors.push(message); });
                    const context = new OfflineAudioContext(1, 4800, 48000);
                    // Offline rendering accepts scheduled nodes before startRendering;
                    // expose the running gate while using its real PCM renderer.
                    Object.defineProperty(context, 'state', {get: () => 'running'});
                    sink.context = context;
                    sink.gain = context.createGain(); sink.gain.connect(context.destination);
                    sink.gain.gain.value = volume;
                    const event = (sequence, timestamp, frequency) => ({sequence: String(sequence),
                        timestamp_ns: String(timestamp), frequency_hz: frequency, duty_permille: frequency ? 500 : 0});
                    const state = {session_id: 'one', status: 'running', board: {
                        timestamp_ns: '100000000', buzzer: {generation: '1', frequency_hz: 0,
                        duty_permille: 0, events: [event(1, 0, 0), event(2, 10000000, 2000), event(3, 20000000, 0)]}}};
                    sink.update(state, true);
                    sink.update(state, true); // Repeated snapshots must not replay.
                    if (action === 'pause') sink.update({...state, status: 'paused'}, true);
                    if (action === 'disconnect') sink.update(state, false);
                    if (action === 'stale') sink.update({...state, observation_stale: true}, true);
                    if (action === 'gap') {
                        state.board.buzzer.events = [event(260, 30000000, 2000)];
                        sink.update(state, true);
                    }
                    const buffer = await context.startRendering(), pcm = buffer.getChannelData(0);
                    function rms(a, b) {
                        const slice = pcm.slice(Math.round(a * 48000), Math.round(b * 48000));
                        return Math.sqrt(slice.reduce((sum, v) => sum + v * v, 0) / slice.length);
                    }
                    let rising = 0;
                    for (let i = 2401; i < 2880; i++) if (pcm[i-1] <= 0 && pcm[i] > 0) rising++;
                    return {before: rms(0, .049), during: rms(.051, .059), after: rms(.061, .1), rising, errors};
                }
                const full = await render(1), quiet = await render(.2);
                const stopped = {};
                for (const action of ['pause', 'disconnect', 'stale', 'gap']) stopped[action] = await render(1, action);
                return {full, quiet, stopped};
            }""")
            assert result["full"]["before"] == result["full"]["after"] == 0
            assert 19 <= result["full"]["rising"] <= 21, result
            assert .45 < result["full"]["during"] < .55, result
            assert abs(result["quiet"]["during"] / result["full"]["during"] - .2) < 1e-6
            for action, values in result["stopped"].items():
                assert values["during"] == 0, (action, values)
                assert bool(values["errors"]) == (action == "gap"), (action, values)
            (args.output / "audio.json").write_text(json.dumps({"status": "passed", **result}, indent=2))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
