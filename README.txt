ANCIENT YOUTUBE AUTO — RESUMABLE DOCUMENTARY PIPELINE
======================================================

PURPOSE
Create fresh ancient-human documentary topics, two narration parts,
Gemini AI Studio Enzo voiceovers, numbered Nano Banana / Flow images,
and a synchronized 1080p YouTube documentary.

IMPORTANT: This is a browser-driven prototype, not a verified fully autonomous
production system. Google AI Studio, ChatGPT and Google Flow can change their UI.
AI Studio may open to a gallery first. The automation clicks Create new dialog, then uses Speaker 1, Style and Run.
Storyteller > Enzo still needs testing in YOUR signed-in browser. If a control cannot be positively
identified, the program STOPS with screenshots instead of choosing a wrong
voice or paying for extra generation. It does not bypass logins/CAPTCHAs.

SETUP (Windows PowerShell)
  Expand-Archive "$HOME\Downloads\AncientYouTubeAuto.zip" "$HOME\Downloads" -Force
  cd "$HOME\Downloads\AncientYouTubeAuto"
  .\Install.ps1

Chrome must be running with remote debugging on port 9333 and logged in to:
   https://chatgpt.com/
   https://aistudio.google.com/generate-speech?model=gemini-3.8-flash-tts
   https://flow.google.com/project/993384d2-016a-442c-89d2-1edbddbab327
Use the Chrome startup from your existing FlowPromptAgent setup if needed.

FIRST — ZERO-COST CONFIGURATION CHECK
  .\Start-AutoVideo.ps1 -DryRun

RUN ONE COMPLETE VIDEO
  .\Start-AutoVideo.ps1

START SMALL — GENERATE SCRIPT AND STOP, THEN RESUME
  .\Start-AutoVideo.ps1 -StopAfter scripts
  .\Start-AutoVideo.ps1

TEST THE IMAGE LOOP — STOP AFTER THE FIRST 5 IMAGES
  .\Start-AutoVideo.ps1 -MaxBatches 1
  .\Start-AutoVideo.ps1

CONTINUOUS NEW-VIDEO LOOP (POTENTIALLY SIGNIFICANT COSTS)
  .\Start-AutoVideo.ps1 -Videos 0

Project folders (automatically named by date and title):
  C:\YouTubeAI\Projects\YYYYMMDD-HHMMSS-topic-name\
      project.json
      script\part_1.txt, part_2.txt
      audio\part_1.wav, part_2.wav (or downloaded audio formats)
      images\visual_001.jpg ...
      images\prompts\001-005.txt ...
      FlowPromptAgentData\state.json
      edit\scenes.json, scene_map.json, timings_review.csv, transcript.txt
      diagnostics\*.png, *.txt
      final_video.mp4
  C:\YouTubeAI\Projects\_AncientYouTubeAuto\history.json, current.json

Safety:
  - Never edits C:\YouTubeAI\images or C:\YouTubeAI\FlowPromptAgentData.
  - Creates a new local folder and a new ChatGPT conversation per video.
  - Records each used title and rejects exact repeats of used topics.
  - The previous Ancient Humans Without Doctors topic is pre-excluded.
  - One image prompt per narration sentence: all are numbered, 5 per batch.
  - Existing Flow agent keeps its protected 2K image journal and match safeguards.
  - Audio generation is NOT re-submitted after an uncertain attempt.
  - If a browser download fails after generation, save the original audio manually
    as audio\part_1.wav or .mp3 inside that CURRENT project's folder and rerun.
  - A failed Whisper transcript match stops rendering rather than inventing cuts.
  - Resume by running the same command again; previously completed files are kept.
  - To explicitly retry an audio part, after verifying it did not generate:
       .\Start-AutoVideo.ps1 -RetryVoicePart 1
    (or part 2). This may incur another generation charge.

COST / TIME
  Many sentences means many image generations. No fixed upper quota;
  use MaxBatches for a test. AI Studio voice usage and Flow jobs may have
  limits or charges. One video can require dozens to >100 Flow images.
  On an 8GB GPU / CPU Whisper small, transcription+1080p rendering can take time.

Limitations:
  - ChatGPT can supply wrong historical claims; no fully automated fact-check.
  - "New" compares known titles, not every publicly existing YouTube video.
  - UI selectors cannot be verified in the assistant's environment.
  - AI Studio may not allow generating long parts in one request; if so, the
    run stops to avoid silently truncated narration.
  - PDF guidelines ask for 1800-2500 words but also describe 8-12min videos.
    This pipeline prioritizes user's 2-part / ~10-12min request: 700-900 words each.
    Edit pipeline.py's script_request to change target duration.

Included:
  pipeline.py, browser_ops.py, bundled FlowPromptAgent modules,
  sync/sync_video.py (Whisper sentence-to-visual alignment), source guide.
