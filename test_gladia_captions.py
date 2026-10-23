"""Offline tests: no microphone and no external Gladia calls."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock, MagicMock
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


class AuthenticationTests(unittest.IsolatedAsyncioTestCase):
    def test_environment_key_trims_whitespace_and_surrounding_quotes(self):
        for value in ('  valid-test-key  ', '"valid-test-key"', "'valid-test-key'"):
            with self.subTest(value=value), \
                    patch.dict(app.os.environ, {"GLADIA_API_KEY": value}, clear=True), \
                    patch.object(app.getpass, "getpass") as prompt:
                self.assertEqual(app.read_api_key("GLADIA_API_KEY", "Gladia"), "valid-test-key")
                prompt.assert_not_called()

    def test_bad_paste_is_rejected_without_exposing_the_key(self):
        for key in ("secret key", "secret\nkey", "secret\x00key", "secret\u200bkey", "<api-key>"):
            with self.subTest(key=repr(key)), \
                    patch.dict(app.os.environ, {}, clear=True), \
                    patch.object(app.sys.stdin, "isatty", return_value=True), \
                    patch.object(app.getpass, "getpass", return_value=key):
                with self.assertRaises(app.CaptionError) as failure:
                    app.read_api_key("GLADIA_API_KEY", "Gladia")
                self.assertNotIn(key, str(failure.exception))

    def test_replacement_uses_hidden_prompt_and_leaves_environment_unchanged(self):
        with patch.dict(app.os.environ, {"GLADIA_API_KEY": "old-test-key"}, clear=True), \
                patch.object(app.sys.stdin, "isatty", return_value=True), \
                patch.object(app.getpass, "getpass", return_value=' "new-test-key" ') as prompt:
            self.assertEqual(app.read_api_key("GLADIA_API_KEY", "Gladia", force_prompt=True),
                             "new-test-key")
            self.assertEqual(app.os.environ["GLADIA_API_KEY"], "old-test-key")
            prompt.assert_called_once()

    def test_missing_noninteractive_key_has_actionable_error(self):
        with patch.dict(app.os.environ, {}, clear=True), \
                patch.object(app.sys.stdin, "isatty", return_value=False):
            with self.assertRaisesRegex(app.CaptionError, "Set GLADIA_API_KEY"):
                app.read_api_key("GLADIA_API_KEY", "Gladia")

    async def test_http_authentication_errors_are_actionable_and_do_not_echo_secrets(self):
        for status, expected in ((401, app.GladiaAuthenticationError), (403, app.CaptionError)):
            async def rejected(request):
                self.assertEqual(request.headers["X-Gladia-Key"], "private-test-key")
                return web.json_response({"message": "gladia user not found private-test-key"},
                                         status=status)

            service = web.Application()
            service.router.add_post("/init", rejected)
            async with TestServer(service) as server, aiohttp.ClientSession() as client:
                with patch.object(app, "API_URL", str(server.make_url("/init"))):
                    with self.assertRaises(expected) as failure:
                        await app.initiate_session(client, {}, "private-test-key")
            self.assertIn(str(status), str(failure.exception))
            self.assertNotIn("private-test-key", str(failure.exception))

    async def test_rejected_key_can_be_replaced_in_terminal(self):
        with patch.object(app, "initiate_session", new_callable=AsyncMock) as init, \
                patch.object(app.sys.stdin, "isatty", return_value=True), \
                patch.object(app, "read_api_key", return_value="new-test-key"), \
                patch("builtins.print") as output:
            init.side_effect = [app.GladiaAuthenticationError("Gladia HTTP 401"), "wss://example"]
            self.assertEqual(await app.session_with_auth(None, {}, "old-test-key"),
                             ("wss://example", "new-test-key"))
            self.assertEqual([call.args[2] for call in init.call_args_list],
                             ["old-test-key", "new-test-key"])
            self.assertNotIn("new-test-key", str(output.call_args_list))

    async def test_same_rejected_key_is_not_retried(self):
        with patch.object(app, "initiate_session", new_callable=AsyncMock) as init, \
                patch.object(app.sys.stdin, "isatty", return_value=True), \
                patch.object(app, "read_api_key", return_value="old-test-key"), \
                patch("builtins.print"):
            init.side_effect = app.GladiaAuthenticationError("Gladia HTTP 401")
            with self.assertRaisesRegex(app.GladiaAuthenticationError, "same rejected"):
                await app.session_with_auth(None, {}, "old-test-key")
            self.assertEqual(init.await_count, 1)

    async def test_noninteractive_authentication_does_not_prompt_or_retry(self):
        with patch.object(app, "initiate_session", new_callable=AsyncMock) as init, \
                patch.object(app.sys.stdin, "isatty", return_value=False), \
                patch.object(app, "read_api_key") as prompt:
            init.side_effect = app.GladiaAuthenticationError("Gladia HTTP 401")
            with self.assertRaises(app.GladiaAuthenticationError):
                await app.session_with_auth(None, {}, "old-test-key")
            self.assertEqual(init.await_count, 1)
            prompt.assert_not_called()

    async def test_authentication_replacements_are_bounded(self):
        with patch.object(app, "initiate_session", new_callable=AsyncMock) as init, \
                patch.object(app.sys.stdin, "isatty", return_value=True), \
                patch.object(app, "read_api_key", side_effect=["new-key-1", "new-key-2"]) as prompt, \
                patch("builtins.print"):
            init.side_effect = app.GladiaAuthenticationError("Gladia HTTP 401")
            with self.assertRaises(app.GladiaAuthenticationError):
                await app.session_with_auth(None, {}, "old-test-key")
            self.assertEqual(init.await_count, 3)
            self.assertEqual(prompt.call_count, 2)


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


class WindowsAudioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.args = app.parser().parse_args(["--device", "3"])
        self.state = app.CaptionState(Path(self.tmp.name) / "captions.txt")
        self.sd = MagicMock()
        self.sd.PortAudioError = RuntimeError

    async def test_windows_validation_uses_input_id_and_pcm_format(self):
        with patch.object(app.sys, "platform", "win32"), \
                patch.object(app, "windows_audio", return_value=self.sd), \
                patch.object(app, "sources", new_callable=AsyncMock) as linux_sources:
            await app.validate_audio(self.args)
        self.sd.check_input_settings.assert_called_once_with(
            device=3, channels=1, dtype="int16", samplerate=16000)
        linux_sources.assert_not_called()

    async def test_linux_device_name_is_rejected_with_windows_instructions(self):
        self.args.device = "alsa_input.usb-Yamaha.analog-stereo"
        with patch.object(app.sys, "platform", "win32"), \
                patch.object(app, "windows_audio", return_value=self.sd):
            with self.assertRaisesRegex(app.CaptionError, "start.cmd --list-devices"):
                await app.validate_audio(self.args)

    async def test_default_and_named_devices(self):
        self.assertIsNone(app.windows_device("@DEFAULT_SOURCE@"))
        self.assertEqual(app.windows_device("Yamaha MME"), "Yamaha MME")

    async def test_windows_list_excludes_output_devices(self):
        self.sd.query_devices.return_value = [
            {"name": "Yamaha", "hostapi": 0, "max_input_channels": 2},
            {"name": "Speakers", "hostapi": 0, "max_input_channels": 0}]
        self.sd.query_hostapis.return_value = [{"name": "MME"}]
        with patch.object(app.sys, "platform", "win32"), \
                patch.object(app, "windows_audio", return_value=self.sd), \
                patch("builtins.print") as output:
            await app.list_devices()
        text = " ".join(str(call) for call in output.call_args_list)
        self.assertIn("0: Yamaha [MME]", text)
        self.assertNotIn("Speakers", text)

    async def test_capture_yields_pcm_and_closes_when_consumer_stops(self):
        pcm = b"\x01\x00" * app.FRAMES

        def open_stream(**kwargs):
            self.assertEqual(kwargs["samplerate"], 16000)
            self.assertEqual(kwargs["channels"], 1)
            self.assertEqual(kwargs["dtype"], "int16")
            self.assertEqual(kwargs["device"], 3)
            kwargs["callback"](pcm, app.FRAMES, None, "")
            return self.sd.stream

        self.sd.RawInputStream.side_effect = open_stream
        with patch.object(app.sys, "platform", "win32"), \
                patch.object(app, "windows_audio", return_value=self.sd):
            frames = app.audio_frames(self.args, asyncio.Event(), self.state)
            self.assertEqual(await anext(frames), pcm)
            await frames.aclose()
        self.sd.stream.__exit__.assert_called_once()

    async def test_overflow_stops_capture_and_closes_stream(self):
        def open_stream(**kwargs):
            kwargs["callback"](b"\x00\x00" * app.FRAMES, app.FRAMES, None, "input overflow")
            return self.sd.stream

        self.sd.RawInputStream.side_effect = open_stream
        with patch.object(app, "windows_audio", return_value=self.sd):
            frames = app.windows_audio_frames(self.args, asyncio.Event(), self.state)
            with self.assertRaisesRegex(app.CaptionError, "overflow"):
                await anext(frames)
        self.sd.stream.__exit__.assert_called_once()

    async def test_windows_demo_starts_and_restores_signal_handlers(self):
        args = app.parser().parse_args(["--demo", "--duration", "0.03"])
        args.output = self.state.output
        site = MagicMock()
        site.start = AsyncMock()
        with patch.object(app.sys, "platform", "win32"), \
                patch.object(app.web, "TCPSite", return_value=site), \
                patch.object(app.signal, "signal", return_value="previous") as install, \
                patch.object(asyncio.get_running_loop(), "add_signal_handler") as unix_signal:
            await app.run(args)
        unix_signal.assert_not_called()
        self.assertEqual(install.call_count, 4)
        self.assertEqual(install.call_args_list[-1].args, (app.signal.SIGTERM, "previous"))
        self.assertEqual(args.output.read_text(), "")


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
