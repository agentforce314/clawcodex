"""Measure DeepSeek prefix-cache efficiency of a real clawcodex session.

Prefix caches bill from the first changed byte onward, so the metric that
matters is not "hit rate" in the abstract but **how many tokens each request
re-sends**. A harness can look healthy at 90% and still be re-billing a
multi-thousand-token block every single turn — that is exactly the bug this
script was written to find (a ~3.4K-token static block sitting in the
relocated tail; see ``build_memory_prompt_parts``).

Two modes:

    # 1. Record: run any clawcodex command, capturing every wire payload.
    python eval/harbor/prefix_cache_probe.py record --out /tmp/pl -- \
        --print --dangerously-skip-permissions \
        --model deepseek-v4-flash --provider deepseek -- "your task"

    # 2. Analyse: diff consecutive payloads and attribute the misses.
    python eval/harbor/prefix_cache_probe.py analyse --out /tmp/pl

``analyse`` reports, per consecutive pair, the longest common message prefix
and the exact bytes that had to be recomputed, alongside the provider's own
``cached_tokens`` so the model of the cache can be checked against reality.
A healthy session diverges only at the append point: everything before the
newest assistant/tool messages is shared, and the relocated tail is small.

Reference points, terminal-bench 2.1, deepseek-v4-flash:
    Reasonix   98.24% hit, ~1,295 miss tokens/request
    clawcodex  90.23% hit, ~6,764 miss tokens/request  (before the split fixes)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import threading


# --------------------------------------------------------------------------- #
# record
# --------------------------------------------------------------------------- #

def _install_recorder(out_dir: str) -> None:
    """Patch the OpenAI SDK so every chat payload lands on disk.

    Hooking the SDK rather than clawcodex's provider means the capture is the
    literal wire content — no risk of measuring a pre-serialisation shape that
    differs from what DeepSeek's cache actually keys on.
    """
    os.makedirs(out_dir, exist_ok=True)
    from openai.resources.chat import completions as _c

    orig = _c.Completions.create
    counter = [0]
    lock = threading.Lock()

    def patched(self, *args, **kwargs):
        with lock:
            counter[0] += 1
            idx = counter[0]
        try:
            with open(os.path.join(out_dir, f"req-{idx:04d}.json"), "w") as fh:
                json.dump(
                    {
                        "idx": idx,
                        "model": kwargs.get("model"),
                        "messages": kwargs.get("messages"),
                        "tools": kwargs.get("tools"),
                    },
                    fh,
                )
        except Exception as exc:  # never break the session being measured
            print(f"[probe] dump failed: {exc}", file=sys.stderr)

        result = orig(self, *args, **kwargs)
        if kwargs.get("stream"):
            return _UsageCapturingStream(result, out_dir, idx)
        _write_usage(out_dir, idx, getattr(result, "usage", None))
        return result

    _c.Completions.create = patched


def _write_usage(out_dir: str, idx: int, usage) -> None:
    if usage is None:
        return
    try:
        with open(os.path.join(out_dir, f"usage-{idx:04d}.json"), "w") as fh:
            json.dump(usage.model_dump(), fh)
    except Exception:
        pass


class _UsageCapturingStream:
    """Transparent proxy that persists the terminal usage chunk."""

    def __init__(self, inner, out_dir, idx):
        self._inner, self._out, self._idx = inner, out_dir, idx

    def __iter__(self):
        for chunk in self._inner:
            _write_usage(self._out, self._idx, getattr(chunk, "usage", None))
            yield chunk

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        return self._inner.close()

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)


def _cmd_record(out_dir: str, argv: list[str]) -> int:
    _install_recorder(out_dir)
    sys.argv = ["clawcodex"] + argv
    from src.cli import main

    return main() or 0


# --------------------------------------------------------------------------- #
# analyse
# --------------------------------------------------------------------------- #

def _norm(msg) -> str:
    return json.dumps(msg, sort_keys=True, ensure_ascii=False)


def _describe(msg, limit=100) -> str:
    content = msg.get("content")
    if isinstance(content, list):
        content = " ".join(
            str(b.get("text") or b.get("type")) for b in content if isinstance(b, dict)
        )
    text = (content or "").replace("\n", "\\n")
    if msg.get("tool_calls"):
        names = ",".join(
            str(tc.get("function", {}).get("name")) for tc in msg["tool_calls"]
        )
        text = f"[tool_calls: {names}] {text}"
    return f"{msg.get('role'):9s} {len(_norm(msg)):7d}ch  {text[:limit]}"


def _cmd_analyse(out_dir: str) -> int:
    requests = []
    for path in sorted(glob.glob(os.path.join(out_dir, "req-*.json"))):
        record = json.load(open(path))
        usage_path = os.path.join(out_dir, f"usage-{record['idx']:04d}.json")
        record["usage"] = (
            json.load(open(usage_path)) if os.path.exists(usage_path) else None
        )
        requests.append(record)

    if not requests:
        print(f"no payloads in {out_dir}")
        return 1

    print(f"{len(requests)} requests in {out_dir}")
    tools_sig = _norm(requests[0].get("tools"))
    print(f"tools payload: {len(tools_sig)} chars, "
          f"{len(requests[0].get('tools') or [])} tools")
    for r in requests[1:]:
        if _norm(r.get("tools")) != tools_sig:
            print(f"!! TOOLS PAYLOAD CHANGED at request {r['idx']} "
                  f"— this busts the whole prefix")

    total_prompt = total_cached = 0
    for r in requests:
        usage = r.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens", usage.get("prompt_cache_hit_tokens", 0))
        total_prompt += usage.get("prompt_tokens", 0)
        total_cached += cached or 0

    for a, b in zip(requests, requests[1:]):
        na = [_norm(m) for m in a["messages"]]
        nb = [_norm(m) for m in b["messages"]]
        i = 0
        while i < min(len(na), len(nb)) and na[i] == nb[i]:
            i += 1
        recompute = sum(len(x) for x in nb[i:])
        print(
            f"\nreq {a['idx']}->{b['idx']}: {len(na)}->{len(nb)} msgs | "
            f"common prefix {i} msgs | recompute {recompute} ch "
            f"(~{recompute // 4} tok)"
        )
        if len(na) - i:
            print("  -- invalidated from the OLD request --")
            for m in a["messages"][i:i + 2]:
                print("   ", _describe(m))
        print("  -- recomputed --")
        for m in b["messages"][i:i + 5]:
            print("   ", _describe(m))

    if total_prompt:
        miss = total_prompt - total_cached
        print(
            f"\nWIRE TOTALS: prompt={total_prompt:,} cached={total_cached:,} "
            f"miss={miss:,} hit={total_cached / total_prompt:.2%} "
            f"| avg miss/request={miss // len(requests):,}"
        )
    return 0


def main() -> int:
    # Split on the first bare ``--`` by hand rather than leaning on argparse's
    # REMAINDER: REMAINDER swallows every later flag, so ``--out`` placed after
    # the mode would silently land in the child argv and the probe would write
    # to its default directory instead.
    argv = sys.argv[1:]
    passthrough: list[str] = []
    if "--" in argv:
        idx = argv.index("--")
        argv, passthrough = argv[:idx], argv[idx + 1:]

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("mode", choices=("record", "analyse"))
    parser.add_argument("--out", default="/tmp/clawcodex-payloads")
    args = parser.parse_args(argv)

    if args.mode == "record":
        return _cmd_record(args.out, passthrough)
    return _cmd_analyse(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
