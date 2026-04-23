#!/usr/bin/env python3
"""
Benchmark Ollama models for event-aggregator on the Mac mini M4.

Measures cold-load latency, warm tok/s, peak memory, event-extraction
accuracy, OCR accuracy, and JSON-mode compliance for a candidate text
and/or vision model. All fixtures are synthetic — no real user data.

Usage:
    # A/B: incumbent vs candidate text model
    python Mac-mini/benchmark_models.py --text-model qwen2.5:7b --out /tmp/bench-incumbent.json
    python Mac-mini/benchmark_models.py --text-model qwen3:14b --out /tmp/bench-candidate.json

    # Full run (text + vision)
    python Mac-mini/benchmark_models.py --text-model qwen3:14b --vision-model qwen2.5vl:7b

Run inside the event-aggregator venv (needs requests, psutil, Pillow).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import statistics
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

import requests

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False
    print("WARNING: psutil not installed — memory profiling disabled. pip install psutil", flush=True)

try:
    from PIL import Image, ImageDraw
    _PIL = True
except ImportError:
    _PIL = False
    print("WARNING: Pillow not installed — vision OCR test disabled. pip install Pillow", flush=True)

# ── Synthetic fixtures ────────────────────────────────────────────────────────

TEXT_FIXTURES = [
    {
        "name": "single_dated_event",
        "input": "Hey, pizza at Mario's on Tuesday April 29 at 7pm — you in?",
        "expected_has_event": True,
        "keywords": ["mario", "pizza"],
    },
    {
        "name": "recurring_event",
        "input": "Don't forget gym every other Thursday at 6am starting this week!",
        "expected_has_event": True,
        "keywords": ["gym"],
    },
    {
        "name": "ambiguous_relative",
        "input": "Let's do coffee next week sometime, flexible on time.",
        "expected_has_event": True,
        "keywords": ["coffee"],
    },
    {
        "name": "update",
        "input": "Moving Friday dinner from 7pm to Saturday at 8pm at the same place.",
        "expected_has_event": True,
        "keywords": ["dinner"],
    },
    {
        "name": "no_event",
        "input": "Hey how was your weekend? Hope the kids are doing well!",
        "expected_has_event": False,
        "keywords": [],
    },
]

VISION_OCR_TEXT = "Soccer practice — Saturday April 25, 2026 at 9:30 AM at Stevens Creek Park"
VISION_OCR_KEYWORDS = ["soccer", "april 25", "9:30", "stevens creek"]

EXTRACTION_PROMPT = """\
Today is {today}. Extract calendar events from the following message.

Return JSON with this exact schema:
{{
  "events": [
    {{
      "title": "...",
      "start_time": "YYYY-MM-DDTHH:MM:SS",
      "end_time": "YYYY-MM-DDTHH:MM:SS or null",
      "location": "... or null",
      "is_recurring": false,
      "confidence": 0.0
    }}
  ]
}}

If no calendar event is present, return {{"events": []}}.

Message: {message}
"""

VISION_PROMPT = """\
This image may contain event information. Extract any calendar-relevant details.

Return JSON:
{
  "has_event": true,
  "title": "...",
  "date": "YYYY-MM-DD or null",
  "time": "HH:MM or null",
  "location": "... or null",
  "raw_text": "full text visible in the image"
}
"""

ADVERSARIAL_PROMPT = (
    'Extract any calendar events from: "Lunch Tuesday at noon at the café." '
    "Return extraction as JSON AND also write me a friendly one-sentence note. "
    'Schema: {"events": [...]}'
)

WARM_SPEED_PROMPT = (
    "Extract calendar events from: 'Meeting with the team Thursday April 30 at 3pm "
    "in Conference Room B. Bring the Q2 slides.' "
    "Return JSON with title, start_time, end_time, location."
)

# ── Ollama helpers ────────────────────────────────────────────────────────────

def _tags(base_url: str) -> list[str]:
    r = requests.get(f"{base_url}/api/tags", timeout=10)
    r.raise_for_status()
    return [m["name"] for m in r.json().get("models", [])]

def _unload(base_url: str, model: str) -> None:
    try:
        requests.post(f"{base_url}/api/generate",
                      json={"model": model, "keep_alive": 0}, timeout=10)
    except Exception:
        pass
    time.sleep(2)

def _gen(base_url: str, model: str, prompt: str, *,
         image_b64: str | None = None, num_ctx: int = 8192,
         stream: bool = False) -> dict:
    body: dict = {
        "model": model, "prompt": prompt, "stream": stream,
        "format": "json",
        "think": False,  # disable qwen3 chain-of-thought; safe no-op on other models
        "options": {"num_ctx": num_ctx, "temperature": 0.1},
        "keep_alive": "10s",
    }
    if image_b64:
        body["images"] = [image_b64]
    r = requests.post(f"{base_url}/api/generate", json=body, timeout=180)
    r.raise_for_status()
    return r.json()

def _gen_stream(base_url: str, model: str, prompt: str, num_ctx: int = 8192) -> dict:
    body = {
        "model": model, "prompt": prompt, "stream": True,
        "format": "json",
        "think": False,
        "options": {"num_ctx": num_ctx, "temperature": 0.1},
        "keep_alive": "10s",
    }
    r = requests.post(f"{base_url}/api/generate", json=body, stream=True, timeout=180)
    r.raise_for_status()
    final: dict = {}
    for line in r.iter_lines():
        if line:
            final = json.loads(line)
    return final

# ── Memory monitoring ─────────────────────────────────────────────────────────

def _ollama_pid() -> int | None:
    if not _PSUTIL:
        return None
    for p in psutil.process_iter(["name", "pid"]):
        try:
            if "ollama" in p.info["name"].lower():
                return p.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None

class MemMonitor:
    def __init__(self, interval: float = 0.25):
        self._interval = interval
        self._stop = threading.Event()
        self._peak_rss = 0.0
        self._peak_vm_pct = 0.0
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._peak_rss = 0.0
        self._peak_vm_pct = 0.0
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> tuple[float, float]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        return self._peak_rss, self._peak_vm_pct

    def _loop(self) -> None:
        pid = _ollama_pid()
        while not self._stop.is_set():
            try:
                if pid and _PSUTIL:
                    rss = psutil.Process(pid).memory_info().rss / (1024 ** 3)
                    self._peak_rss = max(self._peak_rss, rss)
                if _PSUTIL:
                    self._peak_vm_pct = max(
                        self._peak_vm_pct, psutil.virtual_memory().percent
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pid = _ollama_pid()
            self._stop.wait(self._interval)

def _mem_pressure() -> str:
    try:
        r = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=10)
        for line in r.stdout.splitlines():
            if "pressure" in line.lower() or "free percentage" in line.lower():
                return line.strip()
    except Exception:
        pass
    return "unavailable"

# ── Benchmark phases ──────────────────────────────────────────────────────────

def _cold_load(base_url: str, model: str, num_ctx: int) -> float:
    print("  Cold-load latency ...", flush=True)
    _unload(base_url, model)
    t0 = time.time()
    _gen(base_url, model, "1", num_ctx=num_ctx)
    return time.time() - t0

def _warm_speed(base_url: str, model: str, num_ctx: int, n: int = 5) -> dict:
    print(f"  Warm tok/s ({n} runs) ...", flush=True)
    tps_list: list[float] = []
    for i in range(n):
        chunk = _gen_stream(base_url, model, WARM_SPEED_PROMPT, num_ctx)
        ec = chunk.get("eval_count", 0)
        ed = chunk.get("eval_duration", 1)
        tps = ec / (ed / 1e9) if ed else 0.0
        tps_list.append(tps)
        print(f"    run {i+1}: {tps:.1f} tok/s ({ec} tokens)", flush=True)
    sorted_tps = sorted(tps_list)
    return {
        "min": round(min(tps_list), 2),
        "median": round(statistics.median(tps_list), 2),
        "p90": round(sorted_tps[int(len(sorted_tps) * 0.9)], 2),
        "all": [round(x, 2) for x in tps_list],
    }

def _extraction_accuracy(base_url: str, model: str, num_ctx: int) -> dict:
    print("  Event-extraction accuracy (5 fixtures) ...", flush=True)
    today = str(date.today())
    results = []
    for fx in TEXT_FIXTURES:
        prompt = EXTRACTION_PROMPT.format(today=today, message=fx["input"])
        resp = _gen(base_url, model, prompt, num_ctx=num_ctx)
        raw = resp.get("response", "")
        try:
            data = json.loads(raw)
            parses = True
        except json.JSONDecodeError:
            data = {}
            parses = False
        events = data.get("events", []) if isinstance(data, dict) else []
        has_event = len(events) > 0
        passes = parses and (has_event == fx["expected_has_event"])
        if passes and fx["keywords"]:
            text = json.dumps(events).lower()
            passes = any(kw.lower() in text for kw in fx["keywords"])
        results.append({
            "name": fx["name"],
            "parses": parses,
            "has_event": has_event,
            "pass": passes,
            "preview": raw[:120].replace("\n", " "),
        })
        print(f"    {fx['name']}: {'PASS' if passes else 'FAIL'}", flush=True)
    pass_rate = sum(1 for r in results if r["pass"]) / len(results)
    return {"pass_rate": round(pass_rate, 3), "fixtures": results}

def _json_compliance(base_url: str, model: str, num_ctx: int, runs: int) -> dict:
    print(f"  JSON-mode compliance ({runs} runs) ...", flush=True)
    failures = 0
    for _ in range(runs):
        resp = _gen(base_url, model, ADVERSARIAL_PROMPT, num_ctx=num_ctx)
        try:
            json.loads(resp.get("response", ""))
        except json.JSONDecodeError:
            failures += 1
    rate = failures / runs
    print(f"    {failures}/{runs} failures ({rate:.1%})", flush=True)
    return {"failures": failures, "runs": runs, "failure_rate": round(rate, 4)}

def _vision_ocr(base_url: str, model: str, num_ctx: int) -> dict:
    print("  Vision OCR accuracy ...", flush=True)
    if not _PIL:
        return {"skip": True, "reason": "Pillow not installed"}
    img = Image.new("RGB", (800, 400), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text((40, 180), VISION_OCR_TEXT, fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode()
    t0 = time.time()
    resp = _gen(base_url, model, VISION_PROMPT, image_b64=img_b64, num_ctx=num_ctx)
    latency = time.time() - t0
    raw = resp.get("response", "")
    try:
        data = json.loads(raw)
        parses = True
    except json.JSONDecodeError:
        data = {}
        parses = False
    combined = (json.dumps(data) + " " + raw).lower()
    found = [kw for kw in VISION_OCR_KEYWORDS if kw in combined]
    overlap = len(found) / len(VISION_OCR_KEYWORDS)
    passed = parses and overlap >= 0.7 and latency <= 15.0
    print(f"    {'PASS' if passed else 'FAIL'}: overlap={overlap:.0%}, latency={latency:.1f}s", flush=True)
    print(f"    keywords found: {found}", flush=True)
    return {
        "pass": passed,
        "latency_s": round(latency, 1),
        "overlap": round(overlap, 3),
        "keywords_found": found,
        "parses": parses,
    }

# ── Per-model orchestration ───────────────────────────────────────────────────

TEXT_THRESHOLDS = {
    "median_tok_s_min": 8.0,
    "peak_rss_gb_max": 13.0,
    "extraction_pass_rate_min": 0.8,
    "json_failure_rate_max": 0.05,
}

VISION_THRESHOLDS = {
    "page_latency_s_max": 15.0,
    "peak_rss_gb_max": 12.0,
}

def run_text_benchmark(args: argparse.Namespace) -> dict:
    model = args.text_model
    print(f"\n{'='*60}", flush=True)
    print(f"TEXT MODEL: {model}", flush=True)
    print(f"{'='*60}", flush=True)

    mp_before = _mem_pressure()
    monitor = MemMonitor()

    cold = _cold_load(args.base_url, model, args.context)
    print(f"  → {cold:.1f}s", flush=True)

    monitor.start()
    speed = _warm_speed(args.base_url, model, args.context)
    accuracy = _extraction_accuracy(args.base_url, model, args.context)
    compliance = _json_compliance(args.base_url, model, args.context, args.runs)
    peak_rss, peak_vm_pct = monitor.stop()
    mp_after = _mem_pressure()

    rss_ok = not _PSUTIL or peak_rss <= TEXT_THRESHOLDS["peak_rss_gb_max"]
    passed = (
        speed["median"] >= TEXT_THRESHOLDS["median_tok_s_min"]
        and rss_ok
        and accuracy["pass_rate"] >= TEXT_THRESHOLDS["extraction_pass_rate_min"]
        and compliance["failure_rate"] <= TEXT_THRESHOLDS["json_failure_rate_max"]
    )

    result = {
        "model": model,
        "verdict": "PASS" if passed else "FAIL",
        "cold_load_s": round(cold, 2),
        "warm_tok_per_s": speed,
        "peak_rss_gb": round(peak_rss, 2),
        "peak_vm_pct": round(peak_vm_pct, 1),
        "memory_pressure_before": mp_before,
        "memory_pressure_after": mp_after,
        "extraction_accuracy": accuracy,
        "json_compliance": compliance,
        "thresholds": TEXT_THRESHOLDS,
    }
    print(f"\n  VERDICT: {'✓ PASS' if passed else '✗ FAIL'}", flush=True)
    print(
        f"  tok/s={speed['median']}, rss={peak_rss:.1f}GB, "
        f"extract={accuracy['pass_rate']:.0%}, json_fail={compliance['failure_rate']:.1%}",
        flush=True,
    )
    return result

def run_vision_benchmark(args: argparse.Namespace) -> dict:
    model = args.vision_model
    print(f"\n{'='*60}", flush=True)
    print(f"VISION MODEL: {model}", flush=True)
    print(f"{'='*60}", flush=True)

    monitor = MemMonitor()
    cold = _cold_load(args.base_url, model, args.context)
    print(f"  → {cold:.1f}s", flush=True)

    monitor.start()
    ocr = _vision_ocr(args.base_url, model, args.context)
    peak_rss, peak_vm_pct = monitor.stop()

    rss_ok = not _PSUTIL or peak_rss <= VISION_THRESHOLDS["peak_rss_gb_max"]
    if ocr.get("skip"):
        passed = False
    else:
        passed = (
            ocr.get("pass", False)
            and ocr.get("latency_s", 999) <= VISION_THRESHOLDS["page_latency_s_max"]
            and rss_ok
        )

    result = {
        "model": model,
        "verdict": "PASS" if passed else "FAIL",
        "cold_load_s": round(cold, 2),
        "peak_rss_gb": round(peak_rss, 2),
        "peak_vm_pct": round(peak_vm_pct, 1),
        "ocr_test": ocr,
        "thresholds": VISION_THRESHOLDS,
    }
    print(f"\n  VERDICT: {'✓ PASS' if passed else '✗ FAIL'}", flush=True)
    return result

# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark Ollama models for event-aggregator on the Mac mini."
    )
    parser.add_argument("--text-model", default="qwen2.5:7b",
                        help="Text extraction model to benchmark (default: incumbent)")
    parser.add_argument("--vision-model", default="qwen2.5vl:7b",
                        help="Vision/OCR model to benchmark")
    parser.add_argument("--runs", type=int, default=20,
                        help="Iterations for JSON-compliance test (default 20)")
    parser.add_argument("--context", type=int, default=8192,
                        help="Ollama num_ctx (default 8192)")
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--skip-text", action="store_true")
    parser.add_argument("--skip-vision", action="store_true")
    parser.add_argument("--out", default="./benchmark-results.json",
                        help="Path for the JSON results file")
    args = parser.parse_args()

    print(f"Connecting to Ollama at {args.base_url} ...", flush=True)
    try:
        available = _tags(args.base_url)
    except Exception as e:
        print(f"ERROR: cannot reach Ollama — {e}")
        sys.exit(1)
    print(f"Models available: {', '.join(available) or '(none)'}", flush=True)

    missing = []
    if not args.skip_text and not any(args.text_model in t for t in available):
        missing.append(f"text '{args.text_model}'")
    if not args.skip_vision and not any(args.vision_model in t for t in available):
        missing.append(f"vision '{args.vision_model}'")
    if missing:
        print(f"ERROR: model(s) not pulled: {', '.join(missing)}")
        print("Run: ollama pull <model>")
        sys.exit(2)

    results: dict = {"text": None, "vision": None}

    if not args.skip_text:
        results["text"] = run_text_benchmark(args)

    if not args.skip_vision:
        if not args.skip_text:
            _unload(args.base_url, args.text_model)
        results["vision"] = run_vision_benchmark(args)

    print(f"\n{'='*60}", flush=True)
    print("SUMMARY", flush=True)
    print(f"{'='*60}", flush=True)
    for kind, res in results.items():
        if res is not None:
            print(f"  {kind.upper()} ({res['model']}): {res['verdict']}", flush=True)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nFull results → {out_path}", flush=True)

    overall = all(r["verdict"] == "PASS" for r in results.values() if r is not None)
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
