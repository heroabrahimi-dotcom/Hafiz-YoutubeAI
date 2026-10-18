# AncientYouTubeAuto

**Automated AI Image Generation & YouTube Visual Production Pipeline**

AncientYouTubeAuto is a Python-based automation project designed to streamline the creation of AI-generated visuals for YouTube videos.

The system connects a ChatGPT prompt-generation workflow with Google Flow, allowing image prompts to be submitted in batches, images to be downloaded in 2K resolution, and completed images to be removed from the Google Flow workspace.

The main objective is to minimise repetitive manual work while maintaining an organised and reliable content-production pipeline.

## Key Features

- Automated processing of image-generation prompts
- Batch submission of 15 prompts
- Reverse-order submission for efficient image retrieval
- Automatic 2K image downloading
- Automatic removal of successfully downloaded images
- Continuous generation of new ChatGPT prompt batches
- Support for existing and future projects
- Download verification before deletion
- Recovery and journal tracking
- Browser automation using Python

## How the Automation Works

### Phase 1 — Generate Image Prompts

ChatGPT prepares 15 detailed image prompts based on a YouTube story or documentary script.

Each prompt represents a specific scene and follows the project's chosen visual style.

### Phase 2 — Submit Prompts in Reverse Order

The automation sends the 15 image prompts to Google Flow in reverse numerical order.

**Submission order:**

15 → 14 → 13 → ... → 3 → 2 → 1

This arrangement is intentional. When newly generated images appear at the top of Google Flow, the image for prompt 1 should be positioned first, allowing efficient top-to-bottom processing.

### Phase 3 — Download and Remove Images

The automation processes images starting from the top of the Google Flow workspace.

For each image:

1. Select the top image.
2. Open the Download menu.
3. Select **2K** resolution.
4. Wait for the download to finish.
5. Verify the downloaded file exists and is complete.
6. Open the three-dot menu.
7. Select **Move to bin**.
8. Continue with the next top image.

**Important:** An image must never be moved to the bin unless its download has been successfully verified.

### Phase 4 — Prepare the Next Batch

While the current batch is being processed, ChatGPT prepares the next 15 prompts.

The intended pipeline overlaps prompt preparation with Google Flow processing to reduce idle time.

### Phase 5 — Continuous Automation

The workflow repeats:

**Generate prompts → Submit in reverse → Download 2K → Verify → Move to bin → Next batch**

The intended design supports both ongoing projects and new projects without unnecessary repetition of completed work.

## Project Structure

```text
AncientYouTubeAuto/
│
├── pipeline.py
│
├── flow_agent/
│   ├── batch_agent.py
│   ├── agent.py
│   ├── journal.py
│   └── config.json
│
└── README.md
```

### Main Components

| File | Responsibility |
|---|---|
| `pipeline.py` | Overall pipeline coordination |
| `flow_agent/batch_agent.py` | Batch processing and image workflow |
| `flow_agent/agent.py` | Browser automation and Google Flow interaction |
| `flow_agent/journal.py` | Progress tracking and recovery records |
| `flow_agent/config.json` | Automation configuration |

These descriptions represent the intended responsibilities; the exact implementation should be checked against the current source files.

## Operating Modes

### Fast Mode — Preferred Workflow

Fast Mode is designed to minimise unnecessary browser actions.

It follows the defined top-to-bottom workflow without repeatedly matching every visible image against historical prompts.

The priority is efficient image processing while maintaining download verification and project boundaries.

### Safe Resume

Safe Resume handles interrupted sessions and previously recorded work.

However, previously submitted images should not automatically force the entire pipeline into a slow prompt-matching workflow.

The preferred behaviour is to identify the current project's pending items and continue using the fast processing method wherever it is safe.

## Safety Requirements

The automation must:

- Confirm that each downloaded image is complete before deleting its Google Flow copy.
- Avoid deleting images belonging to unrelated projects.
- Preserve existing downloaded files without accidental overwrites.
- Track processed batches to reduce duplicate submissions.
- Recover from browser interruptions and partial downloads.
- Avoid unnecessary regeneration of already submitted images.
- Record errors and progress for troubleshooting.

**Do not use `-FreshStart` when continuing an existing project.**

## Technologies

- Python
- Playwright browser automation
- Google Chrome / Chrome DevTools Protocol
- Google Flow
- ChatGPT
- JSON configuration and progress tracking

## Local Project Directory

```text
C:\Users\heroa\Downloads\AncientYouTubeAuto
```

The application runs locally on Windows. Browser configuration, output directories, and other environment-specific settings should be verified against the installed version.

## Current Development Priorities

- Reliable reverse-order submission of 15 images
- Fast top-to-bottom image processing
- Verified 2K downloads before moving images to the bin
- Concurrent preparation of the next ChatGPT prompt batch
- Seamless continuation of existing projects
- Reliable recovery after interruptions
- Minimal unnecessary browser interaction

## Project Status

**Active development and testing.**

This README describes the intended production workflow. Individual features, especially concurrent batching, recovery behaviour, and safe deletion, must be verified against the current implementation before unattended operation.

## Purpose

AncientYouTubeAuto was created to explore how AI models and browser automation can work together to reduce repetitive tasks in digital content production.

The long-term objective is a reliable, highly automated pipeline capable of preparing consistent visual assets for YouTube documentaries and animated stories.

---

**Project:** AncientYouTubeAuto  
**Developer:** Hafizullah Abrahimi  
**Category:** AI Automation / Python / Browser Automation / Content Production
