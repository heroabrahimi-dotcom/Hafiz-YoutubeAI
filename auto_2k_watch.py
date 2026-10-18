import argparse, json, re, subprocess, sys, time
from pathlib import Path

def newest_project(root: Path):
    items = [p for p in root.iterdir() if p.is_dir()]
    if not items:
        return None
    return max(items, key=lambda p: p.stat().st_mtime)

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def stable_file(path: Path, wait_sec=2):
    try:
        a = path.stat().st_size
        time.sleep(wait_sec)
        b = path.stat().st_size
        return a == b and a > 5000
    except Exception:
        return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects-root", required=True)
    args = ap.parse_args()

    base = Path(__file__).resolve().parent
    py = Path.home() / "Downloads" / "FlowPromptAgent" / ".venv" / "Scripts" / "python.exe"
    worker = base / "batch_upscale_2k.py"
    projects_root = Path(args.projects_root)

    if not py.is_file():
        print(f"AUTO2K ERROR: python not found: {py}")
        sys.exit(1)
    if not worker.is_file():
        print(f"AUTO2K ERROR: batch_upscale_2k.py not found: {worker}")
        sys.exit(1)
    if not projects_root.is_dir():
        print(f"AUTO2K ERROR: projects root not found: {projects_root}")
        sys.exit(1)

    print(f"AUTO2K WATCHER STARTED: {projects_root}")

    while True:
        try:
            project = newest_project(projects_root)
            if not project:
                time.sleep(5)
                continue

            images = project / "images"
            if not images.is_dir():
                time.sleep(5)
                continue

            state_file = images / "_auto_2k_done.json"
            state = load_json(state_file, {})
            changed = False

            files = sorted(
                [p for p in images.iterdir()
                 if p.is_file() and re.match(r"^visual_\d+\.(jpg|jpeg|png|webp)$", p.name, re.I)]
            )

            for f in files:
                m = re.match(r"^visual_(\d+)\.(jpg|jpeg|png|webp)$", f.name, re.I)
                if not m:
                    continue

                num = int(m.group(1))
                key = f"{num:03d}"

                # Skip if already processed after this exact file size/time
                sig = f"{f.name}|{f.stat().st_size}|{int(f.stat().st_mtime)}"
                if state.get(key) == sig:
                    continue

                if not stable_file(f, wait_sec=2):
                    continue

                print(f"AUTO2K: processing VISUAL {key} in {project.name}")

                cmd = [
                    str(py),
                    str(worker),
                    "--images", str(images),
                    "--visual", str(num),
                    "--apply"
                ]

                proc = subprocess.run(cmd, capture_output=True, text=True)
                if proc.returncode == 0:
                    # refresh signature after replacement/upscale
                    out_candidates = [
                        images / f"visual_{num:03d}.png",
                        images / f"visual_{num:03d}.jpg",
                        images / f"visual_{num}.png",
                        images / f"visual_{num}.jpg",
                    ]
                    existing = next((x for x in out_candidates if x.is_file()), f)
                    new_sig = f"{existing.name}|{existing.stat().st_size}|{int(existing.stat().st_mtime)}"
                    state[key] = new_sig
                    changed = True
                    print(f"AUTO2K DONE: VISUAL {key}")
                else:
                    print(f"AUTO2K FAIL: VISUAL {key}")
                    if proc.stdout:
                        print(proc.stdout.strip())
                    if proc.stderr:
                        print(proc.stderr.strip())

            if changed:
                save_json(state_file, state)

        except Exception as e:
            print(f"AUTO2K WATCH ERROR: {e}")

        time.sleep(4)

if __name__ == "__main__":
    main()
