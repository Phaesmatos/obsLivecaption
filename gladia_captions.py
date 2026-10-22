#!/usr/bin/env python3
"""Gladia live audio -> Italian captions -> OBS, for Linux/PipeWire.

No OBS Python plugin is needed. Run this process and add its local Browser
Source, or point a Text (FreeType 2) source at captions_it.txt.
"""
from __future__ import annotations

import argparse
import asyncio
from array import array
from collections import OrderedDict
from contextlib import aclosing, suppress
import json
import getpass
import math
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import textwrap
import time
from urllib.parse import urlsplit
import wave

try:
    import aiohttp
    from aiohttp import web
except ImportError:
    raise SystemExit("Missing aiohttp. Run: bash install.sh")

ROOT = Path(__file__).resolve().parent
API_URL = "https://api.gladia.io/v2/live?region=eu-west"
RATE = 16000
FRAMES = 1600  # 100 ms, mono signed 16-bit little-endian PCM.


class CaptionError(Exception):
    """Actionable failure; safe to show in the operator console."""


class RetryableError(Exception):
    """Temporary network/service failure."""


def language(value):
    return str(value or "").lower().replace("_", "-").split("-")[0]


def clean_text(value):
    return " ".join(str(value or "").split())


def safe_error(value, key=""):
    text = str(value)
    deepl_key = os.environ.get("DEEPL_API_KEY", "")
    if deepl_key:
        text = text.replace(deepl_key, "[redacted]")
    if key:
        text = text.replace(key, "[redacted]")
    text = re.sub(r"(?:https?|wss?)://[^\s\"'<>]+", "[URL redacted]", text)
    text = re.sub(r"(?i)(token|api[_-]?key)([\"': =]+)[^,\s}]+",
                  r"\1\2[redacted]", text)
    return text[:400]


class CaptionState:
    """Bounded state; originals are visible only when tagged as Italian."""

    def __init__(self, output: Path, clear_after=7.0, width=60, clock=time.monotonic,
                 final_only=False):
        self.output = output
        self.clear_after = clear_after
        self.width = width
        self.clock = clock
        self.final_only = final_only
        self.entries = OrderedDict()
        self.visible = OrderedDict()
        self.watermark = -math.inf
        self.changed_at = 0.0
        self.text = ""
        self.status = "Starting"
        self.audio_db = -90.0
        self.last_event = ""
        self.transcripts = 0
        self.translations = 0
        self.started = clock()

    def write(self, text):
        self.text = text
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_name(self.output.name + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, self.output)

    def clear(self):
        self.visible.clear()
        self.write("")

    def new_session(self):
        self.entries.clear()
        self.watermark = -math.inf
        self.clear()

    def expire(self):
        if self.text and self.clock() - self.changed_at >= self.clear_after:
            self.clear()

    def snapshot(self):
        self.expire()
        return {
            "text": self.text, "status": self.status,
            "audio_db": round(self.audio_db, 1),
            "last_event": self.last_event,
            "transcripts": self.transcripts, "translations": self.translations,
            "elapsed": round(self.clock() - self.started),
            "final_only": self.final_only,
        }

    def _render(self):
        text = " ".join(self.visible.values())
        lines = textwrap.wrap(text, width=self.width, break_long_words=True,
                              break_on_hyphens=False)
        result = "\n".join(lines[-2:])
        if result != self.text:
            self.changed_at = self.clock()
            self.write(result)

    def handle(self, event):
        kind = event.get("type")
        if kind not in ("transcript", "translation"):
            return
        if kind == "translation" and event.get("error"):
            self.last_event = "Translation error; check Gladia account/service"
            return
        data = event.get("data") or {}
        if not isinstance(data, dict):
            return
        if self.final_only and kind == "transcript" and data.get("is_final") is not True:
            return  # Also filter locally in case partials arrive despite the API setting.
        utterance = data.get("utterance") or {}
        if not isinstance(utterance, dict):
            return
        ident = data.get("id") if kind == "transcript" else data.get("utterance_id")
        if not ident:
            return
        ident = str(ident)
        # Both documented events carry the original utterance's start time.
        try:
            order = float(utterance["start"])
        except (KeyError, ValueError, TypeError):
            return  # Never guess ordering for malformed events.
        if not math.isfinite(order):
            return
        self.expire()
        entry = self.entries.setdefault(
            ident, {"order": order, "original": "", "language": "",
                    "final": False, "translation": "", "displayed": ""}
        )
        self.entries.move_to_end(ident)
        while len(self.entries) > 256:
            self.entries.popitem(last=False)

        if kind == "transcript":
            self.transcripts += 1
            if entry["final"] and not data.get("is_final"):
                return  # A late partial must not replace a final result.
            entry["original"] = clean_text(utterance.get("text"))
            entry["language"] = language(utterance.get("language"))
            entry["final"] = bool(data.get("is_final"))
            self.last_event = "Transcript (" + (entry["language"] or "unknown") + ")"
        else:
            translated = data.get("translated_utterance") or {}
            if not isinstance(translated, dict):
                return
            target = language(translated.get("language") or data.get("target_language"))
            if target != "it":
                return
            self.translations += 1
            entry["translation"] = clean_text(translated.get("text"))
            if not entry["language"]:
                entry["language"] = language(
                    utterance.get("language") or data.get("original_language"))
                entry["original"] = clean_text(utterance.get("text"))
            self.last_event = "Italian translation"

        chosen = entry["original"] if entry["language"] == "it" else entry["translation"]
        if not chosen:
            # If a partial was subsequently identified as English, remove it.
            if ident in self.visible:
                del self.visible[ident]
                self._render()
            return
        if order < self.watermark:
            # Keep nearby late translations in chronological order, without
            # replacing the newer speech or resurrecting an expired display.
            recent_orders = sorted(item["order"] for item in self.entries.values())[-6:]
            if not self.visible or order < recent_orders[0]:
                return
        if chosen == entry["displayed"]:
            return  # Duplicate events must not resurrect expired captions.
        entry["displayed"] = chosen
        self.watermark = max(self.watermark, order)
        self.visible[ident] = chosen
        self.visible = OrderedDict(sorted(
            self.visible.items(),
            key=lambda item: self.entries.get(item[0], {"order": -math.inf})["order"]))
        while len(self.visible) > 6:
            self.visible.popitem(last=False)
        self._render()


def audio_level(pcm):
    samples = array("h")
    samples.frombytes(pcm[:len(pcm) // 2 * 2])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return -90.0
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    return max(-90.0, 20 * math.log10(max(rms, 1) / 32768))


async def sources():
    if not shutil.which("pactl"):
        raise CaptionError("pactl is missing. On Fedora: sudo dnf install pulseaudio-utils")
    proc = await asyncio.create_subprocess_exec(
        "pactl", "-f", "json", "list", "sources",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), 5)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise CaptionError("The local audio service did not respond.")
    if proc.returncode:
        raise CaptionError("Cannot access the local audio service. Run from a normal "
                           "terminal in your logged-in desktop session.")
    return json.loads(stdout)


async def list_devices():
    for source in await sources():
        name = source.get("name", "")
        label = source.get("description", name)
        suffix = " [speaker/system-audio monitor]" if name.endswith(".monitor") else ""
        print(f"{label}{suffix}\n  {name}")
    print("\nDefault input: @DEFAULT_SOURCE@\n"
          "Use --device 'the exact name above' to select your microphone/mixer.")


async def validate_audio(args):
    if args.wav:
        try:
            with wave.open(str(args.wav), "rb") as recording:
                if (recording.getnchannels(), recording.getsampwidth(),
                    recording.getframerate(), recording.getcomptype()) != (1, 2, RATE, "NONE"):
                    raise CaptionError("--wav needs an uncompressed 16 kHz, 16-bit mono WAV.")
        except (OSError, wave.Error) as exc:
            raise CaptionError(f"Cannot read WAV: {exc}") from None
        return
    if not shutil.which("parec"):
        raise CaptionError("parec is missing. On Fedora: sudo dnf install pulseaudio-utils")
    available = await sources()
    if args.device != "@DEFAULT_SOURCE@" and args.device not in {
            item.get("name") for item in available}:
        raise CaptionError("Audio device not found. Run ./start.sh --list-devices.")
    if not available:
        raise CaptionError("No audio inputs are available.")


async def audio_frames(args, stop, state):
    """Capture via PipeWire's PulseAudio compatibility layer, or a real WAV parser."""
    if args.wav:
        with wave.open(str(args.wav), "rb") as recording:
            deadline = time.monotonic()
            while not stop.is_set():
                pcm = recording.readframes(FRAMES)
                if not pcm:
                    break
                await asyncio.sleep(max(0, deadline - time.monotonic()))
                state.audio_db = audio_level(pcm)
                yield pcm
                deadline += len(pcm) / (RATE * 2)
        return

    proc = await asyncio.create_subprocess_exec(
        "parec", "--raw", "--format=s16le", f"--rate={RATE}", "--channels=1",
        "--latency-msec=100", f"--device={args.device}",
        "--client-name=Gladia OBS captions", "--stream-name=Conference microphone",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        while not stop.is_set():
            try:
                pcm = await asyncio.wait_for(proc.stdout.readexactly(FRAMES * 2), 10)
            except asyncio.IncompleteReadError:
                raise CaptionError("Microphone capture stopped. Check the selected input "
                                   "and PipeWire, then restart.") from None
            except TimeoutError:
                raise CaptionError("No audio frames arrived for 10 seconds. Check the input.") from None
            state.audio_db = audio_level(pcm)
            yield pcm
    finally:
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.communicate(), 2)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    proc.kill()
                await proc.communicate()
        else:
            await proc.communicate()


def load_config(args):
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if tuple(config.get(k) for k in ("encoding", "bit_depth", "sample_rate", "channels")) != (
            "wav/pcm", 16, RATE, 1):
        raise CaptionError("This capture app requires wav/pcm, 16-bit, 16000 Hz, mono.")
    if args.max_segment is not None:
        config["maximum_duration_without_endpointing"] = args.max_segment
    if not 5 <= config.get("maximum_duration_without_endpointing", 5) <= 60:
        raise CaptionError("Gladia maximum_duration_without_endpointing must be 5–60 seconds.")
    if args.captions_only:
        config["realtime_processing"]["named_entity_recognition"] = False
        config["realtime_processing"]["sentiment_analysis"] = False
    if args.final_only:
        config.setdefault("messages_config", {}).update({
            "receive_partial_transcripts": False,
            "receive_final_transcripts": True,
            "receive_realtime_processing_events": True,
        })
    if args.deepl:
        config["realtime_processing"]["translation"] = False
        config["realtime_processing"].pop("translation_config", None)
        config.setdefault("messages_config", {})["receive_final_transcripts"] = True
    return config


class DeepLTranslator:
    """One bounded translation queue per live session; audio keeps streaming."""

    def __init__(self, client, state, key, api="auto"):
        self.client, self.state, self.key = client, state, key
        free = api == "free" or (api == "auto" and key.endswith(":fx"))
        self.url = "https://api-free.deepl.com/v2/translate" if free else (
            "https://api.deepl.com/v2/translate")
        self.queue = asyncio.Queue(maxsize=16)
        self.seen = OrderedDict()
        self.context = []

    def submit(self, event):
        # Ignore Gladia translation events entirely in this mode.
        if event.get("type") != "transcript":
            return
        self.state.handle(event)
        data = event.get("data") or {}
        if not isinstance(data, dict):
            return
        utterance = data.get("utterance") or {}
        if not isinstance(utterance, dict):
            return
        ident = data.get("id")
        if (not ident or data.get("is_final") is not True
                or language(utterance.get("language")) != "en"
                or not clean_text(utterance.get("text"))):
            return
        if ident in self.seen:
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            raise CaptionError("DeepL cannot keep up: translation queue is full. "
                               "Stopped rather than accumulating delayed captions.") from None
        self.seen[ident] = True
        while len(self.seen) > 256:
            self.seen.popitem(last=False)

    async def translate(self, text):
        payload = {"text": [text], "source_lang": "EN", "target_lang": "IT"}
        if self.context:
            payload["context"] = " ".join(self.context)[-2000:]
        for attempt in range(3):
            try:
                async with self.client.post(
                    self.url, headers={"Authorization": "DeepL-Auth-Key " + self.key},
                    json=payload, timeout=aiohttp.ClientTimeout(total=8),
                    allow_redirects=False,
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        try:
                            translated = clean_text(result["translations"][0]["text"])
                        except (KeyError, IndexError, TypeError):
                            raise CaptionError("DeepL returned an invalid translation.") from None
                        if not translated:
                            raise CaptionError("DeepL returned an empty translation.")
                        return translated
                    if response.status == 403:
                        raise CaptionError("DeepL rejected the key. Check DEEPL_API_KEY "
                                           "and --deepl-api free/pro.")
                    if response.status == 456:
                        raise CaptionError("DeepL translation quota exceeded.")
                    if response.status != 429 and response.status < 500:
                        raise CaptionError(f"DeepL HTTP {response.status}; check your API account.")
                    failure = f"DeepL HTTP {response.status}"
            except (aiohttp.ClientError, TimeoutError):
                failure = "DeepL connection timed out or failed"
            if attempt < 2:
                await asyncio.sleep(0.5 * (2 ** attempt))
        raise CaptionError(failure + " after 3 attempts. No English fallback was displayed.")

    async def work(self):
        while True:
            event = await self.queue.get()
            try:
                data = event["data"]
                utterance = data["utterance"]
                original = clean_text(utterance["text"])
                translated = await self.translate(original)
                self.context = (self.context + [original])[-3:]
                self.state.handle({
                    "type": "translation", "error": None,
                    "data": {"utterance_id": data["id"], "utterance": utterance,
                             "translated_utterance": {"language": "it", "text": translated}},
                })
                self.state.last_event = "DeepL Italian translation"
            finally:
                self.queue.task_done()


async def initiate_session(client, config, key):
    async with client.post(API_URL, headers={"X-Gladia-Key": key}, json=config,
                           timeout=aiohttp.ClientTimeout(total=20)) as response:
        if response.status != 201 and response.status != 200:
            detail = safe_error(await response.text(), key)
            message = f"Gladia HTTP {response.status}: {detail}"
            if response.status == 429 or response.status >= 500:
                raise RetryableError(message)
            raise CaptionError(message)
        result = await response.json()
    url = result.get("url", "")
    parsed = urlsplit(url)
    if parsed.scheme != "wss" or not (
            parsed.hostname == "gladia.io" or (parsed.hostname or "").endswith(".gladia.io")):
        raise CaptionError("Gladia returned an unexpected WebSocket URL.")
    return url


async def read_events(socket, state, translator=None):
    async for message in socket:
        if message.type == aiohttp.WSMsgType.TEXT:
            try:
                event = json.loads(message.data)
            except (ValueError, TypeError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "error":
                # Do not print raw server payloads; they can contain session tokens.
                raise CaptionError("Gladia sent an error event. Check your credit balance "
                                   "and live-session configuration in the dashboard.")
            if translator:
                translator.submit(event)
            else:
                state.handle(event)
        elif message.type == aiohttp.WSMsgType.ERROR:
            raise RetryableError("Gladia WebSocket connection failed.")


async def receive_events(socket, state, translator=None):
    if translator is None:
        return await read_events(socket, state)

    async def read_and_drain():
        await read_events(socket, state, translator)
        await translator.queue.join()

    reader = asyncio.create_task(read_and_drain())
    worker = asyncio.create_task(translator.work())
    try:
        done, _ = await asyncio.wait([reader, worker], return_when=asyncio.FIRST_COMPLETED)
        if worker in done:
            await worker  # Propagate translation errors, without switching providers.
        await reader
    finally:
        for task in (reader, worker):
            task.cancel()
        await asyncio.gather(reader, worker, return_exceptions=True)


async def send_frames(socket, frames):
    async with aclosing(frames):
        async for pcm in frames:
            await socket.send_bytes(pcm)


async def stream_connection(socket, frames, state, stop, drain_seconds=15, translator=None):
    """One socket reader; drain final translations after stop_recording."""
    sender = asyncio.create_task(send_frames(socket, frames))
    receiver = asyncio.create_task(receive_events(socket, state, translator))
    stopper = asyncio.create_task(stop.wait())
    tasks = [sender, receiver, stopper]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if sender in done:
            sender.result()
        if receiver in done:
            receiver.result()
            if not stop.is_set() and sender not in done:
                raise RetryableError("Gladia closed the connection unexpectedly.")
        if stopper in done:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        state.status = "Finishing final captions"
        if not socket.closed:
            await socket.send_json({"type": "stop_recording"})
            try:
                await asyncio.wait_for(receiver, drain_seconds)
            except TimeoutError:
                print("Final-event wait ended after 15 seconds.", flush=True)
        stop.set()  # EOF of a WAV is also a normal shutdown.
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_live(args, state, stop, config):
    key = os.environ.get("GLADIA_API_KEY", "").strip()
    if not key or key.startswith("<"):
        raise CaptionError("Set GLADIA_API_KEY, or use ./start.sh for a hidden key prompt.")
    deepl_key = os.environ.get("DEEPL_API_KEY", "").strip()
    if args.deepl and not deepl_key:
        if sys.stdin.isatty():
            deepl_key = getpass.getpass("DeepL API key (hidden): ").strip()
        if not deepl_key:
            raise CaptionError("Set DEEPL_API_KEY, or use ./start.sh --deepl for a hidden prompt.")
    await validate_audio(args)
    async with aiohttp.ClientSession() as client:
        for attempt in range(6):
            if stop.is_set():
                return
            state.status = "Connecting to Gladia"
            try:
                url = await initiate_session(client, config, key)
                if stop.is_set():
                    return
                async with client.ws_connect(
                        url, heartbeat=20, max_msg_size=16 * 1024 * 1024) as socket:
                    state.new_session()
                    state.status = "Live · Italian captions"
                    translator = DeepLTranslator(client, state, deepl_key, args.deepl_api) if args.deepl else None
                    if translator:
                        state.status = "Live · DeepL Italian captions"
                    print("Connected. Streaming selected audio to Gladia (EU). Ctrl+C to stop.",
                          flush=True)
                    await stream_connection(socket, audio_frames(args, stop, state), state, stop,
                                            translator=translator)
                return
            except (RetryableError, aiohttp.ClientError, TimeoutError,
                    ConnectionError) as exc:
                if stop.is_set():
                    return
                state.clear()
                if args.wav:
                    raise CaptionError("Connection lost during WAV playback; stopped to "
                                       "avoid replaying the file and consuming more credits.") from None
                if attempt == 5:
                    raise CaptionError("Connection could not be restored after 6 attempts.") from None
                delay = min(2 ** attempt, 16)
                state.status = f"Disconnected · retrying in {delay}s"
                print(f"{safe_error(exc, key)}\nRetrying in {delay}s. Audio during the gap "
                      "will not be replayed.", flush=True)
                try:
                    await asyncio.wait_for(stop.wait(), delay)
                except TimeoutError:
                    pass


def demo_transcript(ident, start, text, lang="it", final=False):
    return {"type": "transcript", "data": {
        "id": ident, "is_final": final,
        "utterance": {"start": start, "end": start + 1, "text": text, "language": lang}}}


async def run_demo(state, stop):
    state.status = "Demo · no microphone or API used"
    number = 0
    while not stop.is_set():
        number += 1
        start = number * 10
        ident = f"it-{number}"
        for text in ["Buongiorno", "Buongiorno a tutti.",
                     "Buongiorno a tutti. Benvenuti alla conferenza."]:
            state.handle(demo_transcript(ident, start, text))
            await asyncio.sleep(0.6)
        state.handle(demo_transcript(ident, start, text, final=True))
        await asyncio.sleep(2)
        ident = f"en-{number}"
        state.handle(demo_transcript(
            ident, start + 5, "Thank you for joining us today.", "en", True))
        await asyncio.sleep(1)
        state.handle({"type": "translation", "error": None, "data": {
            "utterance_id": ident,
            "utterance": {"start": start + 5, "language": "en",
                          "text": "Thank you for joining us today."},
            "translated_utterance": {"language": "it",
                                     "text": "Grazie per essere qui con noi oggi."}}})
        await asyncio.sleep(4)


def make_app(state):
    app = web.Application()

    async def overlay(request):
        return web.FileResponse(ROOT / "overlay.html", headers={"Cache-Control": "no-store"})

    async def panel(request):
        return web.FileResponse(ROOT / "monitor.html", headers={"Cache-Control": "no-store"})

    async def snapshot(request):
        return web.json_response(state.snapshot(), headers={"Cache-Control": "no-store"})

    app.router.add_get("/", panel)
    app.router.add_get("/overlay", overlay)
    app.router.add_get("/state", snapshot)
    return app


async def maintain(state, stop):
    while not stop.is_set():
        state.expire()
        await asyncio.sleep(0.1)


async def check_audio(args):
    await validate_audio(args)
    state = CaptionState(args.output)
    stop = asyncio.Event()
    count = 0
    peak = -90.0
    async with aclosing(audio_frames(args, stop, state)) as frames:
        async for pcm in frames:
            count += len(pcm) // 2
            peak = max(peak, state.audio_db)
            print(f"\rInput RMS: {state.audio_db:6.1f} dBFS", end="", flush=True)
            if count >= args.check_audio * RATE:
                break
    print(f"\nRead {count / RATE:.1f}s of audio; highest chunk RMS {peak:.1f} dBFS. "
          "Nothing saved or sent to Gladia.")
    if count == 0:
        raise CaptionError("No audio frames received.")
    if peak < -60:
        print("Input was very quiet. Speak into the microphone or choose a different device.")


async def run(args):
    if args.list_devices:
        await list_devices()
        return
    if args.check_audio:
        await check_audio(args)
        return
    config = load_config(args)
    state = CaptionState(args.output, args.clear_after, args.width,
                         final_only=args.final_only)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    runner = web.AppRunner(make_app(state), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", args.port).start()
    except OSError as exc:
        await runner.cleanup()
        raise CaptionError(f"Cannot open local port {args.port}: {exc}. "
                           "Stop the other caption process or use --port 8766.") from None
    state.clear()
    print(f"Operator preview: http://127.0.0.1:{args.port}/\n"
          f"OBS Browser Source: http://127.0.0.1:{args.port}/overlay\n"
          f"OBS text file: {args.output}\n", flush=True)
    print("Caption display: " + ("finalized only" if args.final_only else "partial + final"),
          flush=True)
    print("Translation: " + ("DeepL (Gladia translation disabled)" if args.deepl
                             else "Gladia") + (" · simulated in demo" if args.demo else ""),
          flush=True)
    if not args.demo:
        print(f"Maximum segment: {config['maximum_duration_without_endpointing']}s; "
              f"extra analytics: {'off' if args.captions_only else 'as in your config'}",
              flush=True)
    expiry = asyncio.create_task(maintain(state, stop))
    duration_handle = loop.call_later(args.duration, stop.set) if args.duration else None
    worker = asyncio.create_task(run_demo(state, stop) if args.demo
                                 else run_live(args, state, stop, config))
    stopper = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait([worker, stopper], return_when=asyncio.FIRST_COMPLETED)
        if worker in done:
            await worker
        elif args.demo:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        else:
            # The streaming worker sends stop_recording and drains final events.
            try:
                await asyncio.wait_for(worker, 25)
            except TimeoutError:
                print("Shutdown timed out; closed the connection.", flush=True)
    finally:
        stop.set()
        if duration_handle:
            duration_handle.cancel()
        for task in (worker, stopper, expiry):
            if not task.done():
                task.cancel()
        await asyncio.gather(worker, stopper, expiry, return_exceptions=True)
        state.status = "Stopped"
        state.clear()
        await runner.cleanup()
        print("Stopped. Captions cleared.", flush=True)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=ROOT / "gladia_config.json")
    p.add_argument("--output", type=Path, default=ROOT / "captions_it.txt")
    p.add_argument("--device", default="@DEFAULT_SOURCE@", help="Exact pactl source name")
    modes = p.add_mutually_exclusive_group()
    modes.add_argument("--list-devices", action="store_true")
    modes.add_argument("--check-audio", type=float, metavar="SECONDS",
                       help="Local input meter only; no API or recording")
    modes.add_argument("--demo", action="store_true", help="Synthetic captions; no mic/API")
    p.add_argument("--wav", type=Path, help="Use a 16 kHz/16-bit mono PCM WAV instead of mic")
    p.add_argument("--captions-only", action="store_true", help="Disable NER and sentiment")
    p.add_argument("--final-only", action="store_true",
                   help="Show finalized captions only; hide provisional transcripts")
    p.add_argument("--deepl", "--depel", action="store_true",
                   help="Use DeepL for English-to-Italian translation; disable Gladia translation")
    p.add_argument("--deepl-api", choices=("auto", "free", "pro"), default="auto",
                   help="DeepL endpoint; auto selects Free for keys ending in :fx")
    p.add_argument("--max-segment", type=float, help="Override segment limit (5–60 seconds)")
    p.add_argument("--duration", type=float, help="Stop automatically after this many seconds")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--clear-after", type=float, default=7, help="Hide unchanged captions after N seconds")
    p.add_argument("--width", type=int, default=60, help="Characters per line; two lines shown")
    return p


def main():
    p = parser()
    args = p.parse_args()
    if args.width < 20 or args.clear_after <= 0 or not 1 <= args.port <= 65535:
        p.error("Use width >= 20, clear-after > 0 and a valid port.")
    if any(value is not None and value <= 0 for value in (args.duration, args.check_audio)):
        p.error("Durations must be positive.")
    try:
        asyncio.run(run(args))
    except (CaptionError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {safe_error(exc, os.environ.get('GLADIA_API_KEY', ''))}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
