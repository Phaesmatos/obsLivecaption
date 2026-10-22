"""Offline tests: no microphone and no external Gladia calls."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

import gladia_captions as app


def translation(ident, start, text, target="it", original="English words"):
    return {"type": "translation", "error": None, "data": {
        "utterance_id": ident,
        "utterance": {"start": start, "text": original, "language": "en"},
        "translated_utterance": {"text": text, "language": target}}}


class CaptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 100.0
        self.state = app.CaptionState(Path(self.tmp.name) / "captions.txt",
                                      clock=lambda: self.now)

    def test_italian_partials_replace_and_final_cannot_regress(self):
        self.state.handle(app.demo_transcript("a", 0, "Buon"))
        self.state.handle(app.demo_transcript("a", 0, "Buongiorno"))
        self.state.handle(app.demo_transcript("a", 0, "Buongiorno a tutti.", final=True))
        self.state.handle(app.demo_transcript("a", 0, "Buon"))
        self.assertEqual(self.state.text, "Buongiorno a tutti.")
        self.assertEqual(self.state.output.read_text(), self.state.text)

    def test_english_hidden_until_italian_translation(self):
        self.state.handle(app.demo_transcript("a", 0, "Good morning", "en", True))
        self.assertEqual(self.state.text, "")
        self.state.handle(translation("a", 0, "Bonjour", "fr"))
        self.assertEqual(self.state.text, "")
        self.state.handle(translation("a", 0, "Buongiorno"))
        self.assertEqual(self.state.text, "Buongiorno")

    def test_final_only_keeps_previous_caption_until_next_final(self):
        self.state.final_only = True
        self.state.handle(app.demo_transcript("a", 0, "Frase confermata.", final=True))
        self.state.handle(app.demo_transcript("b", 1, "Parole provvisorie errate"))
        self.assertEqual(self.state.snapshot()["text"], "Frase confermata.")
        self.assertEqual(self.state.output.read_text(), "Frase confermata.")
        self.state.handle(app.demo_transcript("b", 1, "Testo corretto.", final=True))
        self.assertEqual(self.state.text, "Frase confermata. Testo corretto.")

    def test_final_only_accepts_completed_translation_before_original(self):
        self.state.final_only = True
        self.state.handle(app.demo_transcript("a", 0, "Unfinished", "en"))
        self.assertEqual(self.state.text, "")
        self.state.handle(translation("a", 0, "Buongiorno."))
        self.state.handle(app.demo_transcript("a", 0, "Good morning.", "en", True))
        self.assertEqual(self.state.text, "Buongiorno.")

    def test_final_only_changes_session_flags_without_editing_saved_config(self):
        args = app.parser().parse_args(["--final-only"])
        config = app.load_config(args)
        self.assertFalse(config["messages_config"]["receive_partial_transcripts"])
        self.assertTrue(config["messages_config"]["receive_final_transcripts"])
        self.assertTrue(config["messages_config"]["receive_realtime_processing_events"])
        original = app.load_config(app.parser().parse_args([]))
        self.assertTrue(original["messages_config"]["receive_partial_transcripts"])

    def test_translation_can_arrive_before_transcript(self):
        self.state.handle(translation("a", 0, "Buongiorno"))
        self.state.handle(app.demo_transcript("a", 0, "Good morning", "en", True))
        self.assertEqual(self.state.text, "Buongiorno")

    def test_italian_is_not_duplicated_by_same_language_translation(self):
        self.state.handle(app.demo_transcript("a", 0, "Ciao.", final=True))
        event = translation("a", 0, "Ciao.")
        event["data"]["utterance"]["language"] = "it"
        self.state.handle(event)
        self.assertEqual(self.state.text, "Ciao.")

    def test_old_translation_cannot_rewind_display(self):
        self.state.handle(app.demo_transcript("a", 0, "Old English", "en", True))
        self.state.handle(app.demo_transcript("b", 10, "Nuova frase.", final=True))
        self.state.handle(translation("a", 0, "Vecchia frase."))
        self.assertEqual(self.state.text, "Vecchia frase. Nuova frase.")

    def test_very_late_translation_does_not_replay_old_speech(self):
        self.state.handle(app.demo_transcript("a", 0, "Old English", "en", True))
        for index in range(1, 9):
            self.state.handle(app.demo_transcript(str(index), index, "Frase recente.", final=True))
        before = self.state.text
        self.state.handle(translation("a", 0, "Vecchia frase."))
        self.assertEqual(self.state.text, before)

    def test_failed_translation_is_visible_to_operator(self):
        self.state.handle({"type": "translation", "error": {"message": "failed"}, "data": None})
        self.assertIn("Translation error", self.state.last_event)
        self.assertEqual(self.state.text, "")

    def test_silence_clears_and_duplicates_do_not_resurrect(self):
        event = app.demo_transcript("a", 0, "Ciao.", final=True)
        self.state.handle(event)
        self.now += 8
        self.assertEqual(self.state.snapshot()["text"], "")
        self.state.handle(event)
        self.assertEqual(self.state.text, "")
        self.assertEqual(self.state.output.read_text(), "")

    def test_language_correction_removes_provisional_original(self):
        self.state.handle(app.demo_transcript("a", 0, "Hello", "it"))
        self.state.handle(app.demo_transcript("a", 0, "Hello", "en", True))
        self.assertEqual(self.state.text, "")
        self.state.handle(translation("a", 0, "Ciao"))
        self.assertEqual(self.state.text, "Ciao")

    def test_unknown_language_original_is_never_displayed(self):
        self.state.handle(app.demo_transcript("a", 0, "Unknown speech", ""))
        self.assertEqual(self.state.text, "")

    def test_long_session_bounded_state_two_lines_and_utf8(self):
        for index in range(500):
            self.state.handle(app.demo_transcript(str(index), index,
                "Perché è così? Questa è una frase italiana abbastanza lunga.", final=True))
        self.assertLessEqual(len(self.state.entries), 256)
        self.assertLessEqual(len(self.state.text.splitlines()), 2)
        self.assertIn("è", self.state.output.read_text(encoding="utf-8"))
        self.state.new_session()
        self.state.handle(app.demo_transcript("new", 0, "Nuova sessione.", final=True))
        self.assertEqual(self.state.text, "Nuova sessione.")

    def test_original_config_preserved_and_optional_overrides(self):
        args = app.parser().parse_args([])
        config = app.load_config(args)
        self.assertEqual(config["endpointing"], 0.08)
        self.assertEqual(config["maximum_duration_without_endpointing"], 10)
        self.assertTrue(config["realtime_processing"]["sentiment_analysis"])
        self.assertTrue(config["realtime_processing"]["named_entity_recognition"])
        self.assertEqual(config["language_config"]["languages"], ["it", "en"])
        self.assertTrue(config["messages_config"]["receive_realtime_processing_events"])
        args = app.parser().parse_args(["--captions-only", "--max-segment", "5"])
        config = app.load_config(args)
        self.assertFalse(config["realtime_processing"]["sentiment_analysis"])
        self.assertEqual(config["maximum_duration_without_endpointing"], 5)
        args.max_segment = 3
        with self.assertRaises(app.CaptionError):
            app.load_config(args)

    def test_credentials_are_redacted(self):
        result = app.safe_error("secret123 wss://api.gladia.io/v2/live?token=abc", "secret123")
        self.assertNotIn("secret123", result)
        self.assertNotIn("abc", result)


class DeepLTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = app.CaptionState(Path(self.tmp.name) / "captions.txt", final_only=True)
        self.requests = []

    def client(self, status=200):
        class Response:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def json(self):
                return {"translations": [{"text": "Buongiorno."}]}
        response = Response()
        response.status = status
        def post(url, **kwargs):
            self.requests.append((url, kwargs))
            return response
        return SimpleNamespace(post=post)

    async def test_flag_disables_only_gladia_translation(self):
        for flag in ("--deepl", "--depel"):
            config = app.load_config(app.parser().parse_args([flag]))
            self.assertFalse(config["realtime_processing"]["translation"])
            self.assertNotIn("translation_config", config["realtime_processing"])
            self.assertTrue(config["messages_config"]["receive_final_transcripts"])
        config = app.load_config(app.parser().parse_args([]))
        self.assertTrue(config["realtime_processing"]["translation"])

    async def test_receive_drains_deepl_before_returning_and_never_displays_english(self):
        translator = app.DeepLTranslator(self.client(), self.state, "test-key:fx")
        events = [
            app.demo_transcript("a", 0, "Wrong partial", "en"),
            app.demo_transcript("a", 0, "Good morning.", "en", True),
            app.demo_transcript("a", 0, "Good morning.", "en", True),
            translation("a", 0, "Ignored Gladia translation"),
        ]
        class Socket:
            async def __aiter__(self):
                for event in events:
                    yield SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(event))
        await app.receive_events(Socket(), self.state, translator)
        self.assertEqual(self.state.output.read_text(), "Buongiorno.")
        self.assertEqual(len(self.requests), 1)
        url, kwargs = self.requests[0]
        self.assertEqual(url, "https://api-free.deepl.com/v2/translate")
        self.assertEqual(kwargs["headers"]["Authorization"], "DeepL-Auth-Key test-key:fx")
        self.assertEqual(kwargs["json"],
                         {"text": ["Good morning."], "source_lang": "EN", "target_lang": "IT"})

    async def test_italian_bypasses_deepl(self):
        translator = app.DeepLTranslator(self.client(), self.state, "test-key")
        translator.submit(app.demo_transcript("a", 0, "Buonasera.", "it", True))
        self.assertEqual(self.state.text, "Buonasera.")
        self.assertTrue(translator.queue.empty())
        self.assertFalse(self.requests)
        self.assertEqual(translator.url, "https://api.deepl.com/v2/translate")

    async def test_context_and_explicit_endpoint(self):
        translator = app.DeepLTranslator(self.client(), self.state, "test-key", "free")
        translator.context = ["Previous English sentence."]
        await translator.translate("Good morning.")
        self.assertEqual(self.requests[0][1]["json"]["context"], "Previous English sentence.")
        self.assertIn("api-free.deepl.com", self.requests[0][0])

    async def test_invalid_key_and_quota_fail_without_fallback(self):
        for status in (403, 456):
            translator = app.DeepLTranslator(self.client(status), self.state, "secret")
            with self.assertRaises(app.CaptionError):
                await translator.translate("Good morning.")
        self.assertEqual(self.state.text, "")

    async def test_transient_failure_is_bounded(self):
        translator = app.DeepLTranslator(self.client(429), self.state, "secret")
        with patch.object(app.asyncio, "sleep", new_callable=AsyncMock):
            with self.assertRaises(app.CaptionError):
                await translator.translate("Good morning.")
        self.assertEqual(len(self.requests), 3)


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = app.CaptionState(Path(self.tmp.name) / "captions.txt")

    async def test_http_init_binary_audio_and_final_translation_drain(self):
        received = []
        stop_received = asyncio.Event()
        config = app.load_config(app.parser().parse_args([]))

        async def init(request):
            self.assertEqual(request.headers["X-Gladia-Key"], "test-key")
            self.assertEqual(await request.json(), config)
            return web.json_response(
                {"id": "test", "url": "wss://api.gladia.io/mock-session"}, status=201)

        async def websocket(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for message in ws:
                if message.type == aiohttp.WSMsgType.BINARY:
                    received.append(message.data)
                    await ws.send_json(app.demo_transcript("a", 0, "Hello", "en", True))
                elif message.type == aiohttp.WSMsgType.TEXT:
                    self.assertEqual(json.loads(message.data), {"type": "stop_recording"})
                    stop_received.set()
                    # Final translation arrives only AFTER the client's stop request.
                    await asyncio.sleep(0.02)
                    await ws.send_json(translation("a", 0, "Ciao a tutti."))
                    await ws.close()
            return ws

        service = web.Application()
        service.router.add_post("/init", init)
        service.router.add_get("/ws", websocket)

        async def frames():
            yield b"\x00\x00" * 1600
            await asyncio.sleep(0.03)
            yield b"\x01\x00" * 1600

        async with TestServer(service) as server, aiohttp.ClientSession() as client:
            with patch.object(app, "API_URL", str(server.make_url("/init"))):
                url = await app.initiate_session(client, config, "test-key")
            self.assertEqual(url, "wss://api.gladia.io/mock-session")
            async with client.ws_connect(server.make_url("/ws")) as ws:
                await app.stream_connection(ws, frames(), self.state, asyncio.Event())
        self.assertTrue(stop_received.is_set())
        self.assertEqual([len(chunk) for chunk in received], [3200, 3200])
        self.assertEqual(self.state.text, "Ciao a tutti.")

    async def test_operator_and_overlay_read_identical_safe_text(self):
        self.state.handle(app.demo_transcript("a", 0, "<script>è una prova</script>", final=True))
        async with TestServer(app.make_app(self.state)) as server, aiohttp.ClientSession() as client:
            async with client.get(server.make_url("/state")) as response:
                data = await response.json()
                self.assertEqual(data["text"], self.state.text)
                self.assertNotIn("key", data)
            async with client.get(server.make_url("/overlay")) as response:
                html = await response.text()
                self.assertIn("captions.textContent", html)
                self.assertNotIn("innerHTML", html)

    async def test_disconnection_closes_audio_generator(self):
        cleaned = asyncio.Event()

        async def frames():
            try:
                while True:
                    yield b"\x00\x00" * 1600
                    await asyncio.sleep(0.01)
            finally:
                cleaned.set()

        async def websocket(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.receive()
            await ws.close()
            return ws

        service = web.Application()
        service.router.add_get("/ws", websocket)
        async with TestServer(service) as server, aiohttp.ClientSession() as client:
            async with client.ws_connect(server.make_url("/ws")) as ws:
                with self.assertRaises((app.RetryableError, ConnectionError, aiohttp.ClientError)):
                    await app.stream_connection(ws, frames(), self.state, asyncio.Event())
        self.assertTrue(cleaned.is_set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
