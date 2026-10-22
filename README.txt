bash start.sh --deepl --final-only --captions-only


GLADIA ITALIAN CAPTIONS FOR OBS

Prepared for this Fedora computer, 22 October 2026

This app captures a microphone or mixer, sends live PCM audio to Gladia,
and displays Italian captions in OBS. Run it beside OBS; do not import it
under Tools > Scripts. No OBS Python plugin or audio virtual cable is needed
for the microphones/mixer connected to this computer.

Audio path:
  Microphone/mixer -> Python -> Gladia live WebSocket
                              -> Italian text -> local overlay -> OBS -> Teams

The Browser Source is recommended. A Text (FreeType 2) file is also provided.
The captions become part of the video, so everyone sees the same Italian
captions. They are not Teams' selectable native captions.


1. OPEN A TERMINAL IN THIS FOLDER

  cd "/home/mousum/Documents/Codex/2026-10-22/okay-so-i-have-a-conference/outputs/gladia-obs"

The necessary Python dependency (aiohttp) and audio tools (parec/pactl)
are already available on this computer. If moved to another computer:

  bash install.sh

On Fedora, missing audio tools can be installed with:

  sudo dnf install pulseaudio-utils

The capture backend is for Linux with PulseAudio or PipeWire's PulseAudio
service. This package does not provide Windows/macOS microphone capture.


2. TRY THE DEMO FIRST — NO KEY, MICROPHONE OR API USAGE

  bash start.sh --demo

Open http://127.0.0.1:8765/ in your browser. This is the operator monitor.
It shows a synthetic conference preview, Italian captions and connection
status. The demo simulates Italian transcription and English translation.

Keep the terminal running while adding the OBS source.


3. ADD THE OVERLAY TO OBS

  a. Select the scene you send to Teams.
  b. In Sources, click + > Browser. Name it "Gladia Italian Captions".
  c. Leave "Local file" OFF.
  d. Set URL to:

       http://127.0.0.1:8765/overlay

  e. Set Width and Height to your OBS base canvas size:
       1920 x 1080 for a 1080p canvas, or 1280 x 720 for 720p.
       Check the canvas under Settings > Video.
  f. Click OK. Right-click the source > Transform > Fit to Screen.
  g. Move this source ABOVE your camera/video sources in the Sources list.
  h. Leave "Shutdown source when not visible" off.

The background is transparent. Captions appear near the bottom, in two
lines of white text with a dark background. The source contains no audio.
Do not use the operator-monitor URL "/" as the OBS source.

Your existing LocalVocal setup can stay installed. To avoid two caption
systems displaying together, disable its audio filter and hide its
"LocalVocal Subtitles" source when using Gladia.

For multiple scenes, use Add Existing to reuse this Browser Source.


4. SELECT AND CHECK THE CONFERENCE MICROPHONE/MIXER

Stop the demo with Ctrl+C.

  bash start.sh --list-devices
  bash start.sh --check-audio 5

Speak into the microphone during the five-second check. It displays an
input level and does not save audio or contact Gladia.

By default, the app uses the desktop's default INPUT device. Change that
in Fedora's Sound settings, or specify an exact source name from the list:

  bash start.sh --device 'EXACT_SOURCE_NAME' --check-audio 5

Use the same --device argument when starting live captions.

Choose the conference mic/mixer input, not a source ending in ".monitor":
monitor sources capture speaker/system playback. Your currently connected
inputs include internal audio, Anker PowerConf C200, EarPods and soundcore.
List devices again after connecting a different conference mixer.

OBS and this app can capture the same input through PipeWire. This app
captures it separately, so OBS filters and OBS mute controls do not mute
or process the Gladia feed. Stop this app to stop uploading audio.


5. START REAL CAPTIONS

To use your supplied configuration, with the required protocol fixes:

  bash start.sh

The launcher asks for your Gladia API key with typing hidden. Paste it
and press Enter. It sets GLADIA_API_KEY only for this process and does not
write the key into any file or put it in the command line.

If GLADIA_API_KEY already exists in your terminal's environment, the
launcher uses it without prompting. The Python file also reads this variable:

  python3 gladia_captions.py

For caption-focused operation and the shorter supported segment limit:

  bash start.sh --captions-only --max-segment 5

That optional command disables named-entity recognition and sentiment
analysis, which do not contribute to the captions, and changes the maximum
segment duration from 10 to 5 seconds. It does not change your saved JSON.

To show only finalized captions to the audience:

  bash start.sh --final-only

Or combine the options:

  bash start.sh --final-only --captions-only --max-segment 5

With --final-only, Italian captions appear only after Gladia marks the
transcript final. Completed Italian translations of English speech still
appear normally. Provisional transcripts are disabled in the API request
and filtered locally. This applies to both the Browser Source and text file.
Audio continues streaming normally; this flag changes caption display only.
Expect captions to appear later because they wait for finalization.
Restart the app with the flag; the OBS source needs no change.
You can preview this behavior with: bash start.sh --demo --final-only

For a chosen mixer, add:

  --device 'EXACT_SOURCE_NAME'

OPTIONAL: USE DEEPL INSTEAD OF GLADIA FOR TRANSLATION

  bash start.sh --deepl --final-only --captions-only

The spelling --depel is also accepted as an alias for --deepl.
The launcher asks privately for your Gladia key and then your DeepL API key,
unless GLADIA_API_KEY and DEEPL_API_KEY are already set in the environment.
Neither key is saved in a file or exposed to the HTML page.

With this flag:
  - Gladia translation is disabled for the session; the saved JSON is unchanged.
  - Final English transcripts go to DeepL with source_lang=EN and target_lang=IT.
  - Italian transcripts display directly; they do not need another translation.
  - --final-only still controls whether provisional Italian text is hidden.
  - Only completed DeepL translations appear for English speech.
  - Up to three previous English segments provide context for subsequent ones.
  - Both the existing OBS overlay and captions_it.txt receive the Italian result.

No OBS source changes are needed. Without --deepl, the original Gladia
translation path remains active. --demo never calls either service, even
when --deepl is present: its translations are synthetic.

The app automatically uses api-free.deepl.com for API keys ending in :fx,
and api.deepl.com otherwise. If your account requires a different endpoint:

  bash start.sh --deepl --deepl-api free --final-only
  bash start.sh --deepl --deepl-api pro --final-only

Use an API key from a DeepL API account, not a web-translator subscription.
Only transcript text is sent to DeepL; the microphone audio still goes to Gladia.
DeepL usage is separate from Gladia transcription usage.

Translation runs in a bounded queue without blocking microphone streaming.
Temporary DeepL failures are retried up to three times. Invalid credentials,
exhausted quota, persistent failures, or a full queue stop the app with an
operator error. It does not silently switch translators or show English text.
Shutdown allows the existing 15-second final-result window to drain queued
translations; anything still pending afterward is cancelled.

DeepL authentication: https://developers.deepl.com/docs/getting-started/auth
DeepL translation: https://developers.deepl.com/api-reference/translate/request-translation

To stop automatically after two hours, add:

  --duration 7200

Wait for "Connected" in the terminal, then test both an Italian sentence
and an English sentence. Watch http://127.0.0.1:8765/ for input level,
transcript counts and Italian translation counts.

The app must remain running for the entire conference. Ctrl+C stops capture,
requests final results from Gladia, then closes and clears the captions.
Stopping OBS or its virtual camera does not stop this separate process.


6. SEND THE RESULT TO TEAMS

In OBS, select the captioned scene and Start Virtual Camera. Set the virtual
camera output to Program, or to the scene containing BOTH video and captions.
Do not select only the original camera source as the virtual-camera output.

In Teams, select OBS Virtual Camera as your camera. Keep the conference
microphone/mixer selected separately as Teams' microphone: OBS Virtual Camera
carries the video. Check the result from another participant's view.

These captions are already in the image; no Teams translation setting or
caption license is needed to view them.


7. OPTIONAL: A NORMAL OBS TEXT SOURCE

Instead of the Browser Source:

  a. Sources > + > Text (FreeType 2).
  b. Enable "Read from file".
  c. Choose this file:

     /home/mousum/Documents/Codex/2026-10-22/okay-so-i-have-a-conference/outputs/gladia-obs/captions_it.txt

  d. Choose a readable white font, about 42–48 px for a 1080p canvas,
     with outline/shadow. Position it at the bottom.
  e. Keep Chat log mode off; the app already writes only two lines.

Use either the browser overlay or text source to avoid duplicate captions.
The browser polls every 150 ms; OBS controls the text-file refresh cadence.
If the process is forcibly killed, the text file can retain its last caption.
Restart the app or hide that source. The browser clears on connection loss.


8. WHAT WAS PRESERVED / FIXED

gladia_config.json preserves:
  - solaria-1; Italian and English with code switching
  - speech threshold 0.5, audio enhancer off
  - endpointing 0.08 seconds, maximum segment duration 10 seconds
  - base translation model, Italian target
  - NER and sentiment analysis enabled
  - your other message flags and callback=false

Required additions:
  - wav/pcm, 16-bit, 16000 Hz, mono: matches the actual live audio
  - receive_realtime_processing_events=true to receive translations
  - receive_errors=true
  - match_original_utterances=true for matching translations to speech

The app creates a live session with POST /v2/live?region=eu-west, then
connects to the WebSocket URL returned by Gladia. It does not use a hard-coded
session token. Audio is sent as 100 ms binary PCM frames.

By default, Italian-tagged partial/final transcripts display directly.
With --final-only, only finalized transcripts display. English originals
are withheld; their Italian translation is displayed when received.
Partial results update in place, duplicate events are ignored, and nearby
late translations are placed in chronological order. Only two recent lines
are displayed. Translations older than the retained six-utterance window
are discarded to avoid replaying old speech.

There is no full local transcript/audio archive. The text file contains only
the visible caption. Gladia receives the audio when live mode is running;
its service/account retention settings are separate from this app.


9. LATENCY, SETTINGS AND TROUBLESHOOTING

English-to-Italian translation is utterance-based. A partial-transcript flag
does not make translated captions word-by-word. A continuous utterance can
reach your 10-second limit before finalization, followed by translation time.
The optional 5-second setting reduces that wait; 5 seconds is the API minimum.
The 0.08-second endpointing value is a silence threshold, not a guaranteed
total caption delay. Actual speed/accuracy needs a live rehearsal.

Short utterances, a clean close microphone, and a stable Internet connection
help. If words are cut into fragments, try a larger endpointing value such
as 0.2 in gladia_config.json and rehearse again. Keep the base translation model
for this low-latency setup. Language detection and provisional text can be
corrected by the service; mixed-language speech may still need revisions.

The service bills according to your Gladia plan/credits. This app cannot
enforce your account's trial balance or disable paid overages. The original
NER/sentiment options remain enabled unless --captions-only is supplied.

If no captions appear:
  - Try --demo. If it works, the OBS overlay connection is working.
  - Check --check-audio and the selected input.
  - If Italian works but English does not, look for Italian translation
    events in the monitor and confirm translation credits/features.
  - HTTP 401/403: check the API key/account permissions.
  - HTTP 400/422: check the configuration and the reported validation error.
  - Check your Gladia dashboard if credits or translation calls fail.

If the browser source is blank after starting the app, open its Properties
and use "Refresh cache of current page". Ensure the URL ends in /overlay.
If port 8765 is occupied, start with --port 8766 and change the OBS URL to
http://127.0.0.1:8766/overlay.

On a temporary connection failure, capture stops and the app retries with
a new live session, up to six attempts. Audio during an outage is not buffered
or replayed; those captions can be lost. Caption state is bounded for long
sessions. Gladia's live session limit is three hours; this app is intended
for your one-to-two-hour conference and does not implement automatic rollover.

Appearance:
  - Edit overlay.html to change font, background or bottom margin.
  - --width 60 controls character wrapping; --clear-after 7 controls timeout.
  - Refresh the OBS Browser Source after editing its HTML/CSS.

An existing WAV can be streamed for rehearsal:

  bash start.sh --wav "/path/to/16khz-mono.wav" --captions-only --max-segment 5

WAV playback is real-time and uses the API. It accepts an uncompressed 16-bit,
16 kHz mono WAV and parses its header correctly.


10. VALIDATION AND OFFICIAL DOCUMENTATION

The included tests run without an API key and without external service calls:

  python3 -m unittest -v test_gladia_captions.py

They cover language filtering, partial/final updates, ordering, duplicate
events, silence expiry, bounded state, config preservation, safe HTML text,
HTTP initialization, binary WebSocket audio and final translation on shutdown.
A local microphone capture and browser preview were also tested on this PC.
Live Gladia authentication, translation quality and end-to-end latency remain
to be verified with your key and conference microphone.

Gladia live initialization:
https://docs.gladia.io/api-reference/v2/live/init
Gladia translation event:
https://docs.gladia.io/api-reference/v2/live/message/translation
Gladia live quickstart:
https://docs.gladia.io/chapters/live-stt/quickstart
OBS Browser Source:
https://obsproject.com/kb/browser-source
OBS Text Sources:
https://obsproject.com/kb/text-sources
OBS Virtual Camera:
https://obsproject.com/kb/virtual-camera-guide
