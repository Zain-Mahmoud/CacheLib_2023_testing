#!/usr/bin/env python3
"""Pull the ML / eviction-controller stats out of a cachebench log.

CacheAllocator::render() prints a stats block every ~10M lookups (starting
with "parsed requests:"). This collects those blocks, plus the final
cachebench summary, into two files next to the log:

  <log>.ml.csv          one row per stats block
  <log>.ml_summary.txt  run totals and averages

Usage: summarize_ml.py <cachebench.log> [<more logs> ...]
"""
import csv
import re
import sys

# block line prefix -> csv column. Times are printed in seconds.
BLOCK_FIELDS = {
    "parsed requests": "parsed_requests",
    "interval": "interval_s",
    "miss rate (period)": "miss_rate",
    "find time": "find_s",
    "insert time": "insert_s",
    "meta update time": "meta_update_s",
    "allocate time": "allocate_s",
    "eviction time": "eviction_s",
    "enqueue time": "enqueue_s",
    "dequeue time": "dequeue_s",
    "release time": "release_s",
    "reinsertion queue": "candidates_scored",
    "eviciton queue": "evictions_by_ml",   # sic, as printed
    "evict queue empty": "evictions_queue_empty",
    "build feature time": "build_feature_s",
    "predict time": "predict_s",
}
DERIVED = [
    "lookups_per_s",
    "predict_us_per_candidate",
    "model_us_per_candidate",
    "evict_queue_empty_frac",
]

# final cachebench summary lines
SUMMARY_PATTERNS = {
    "total_ops_millions": re.compile(r"^Total Ops\s*:\s*([\d.]+) million"),
    "total_sets": re.compile(r"^Total sets\s*:\s*([\d,]+)"),
    "get_per_s": re.compile(r"^get\s*:\s*([\d,]+)/s"),
    "get_hit_pct": re.compile(r"^get\s*:.*success\s*:\s*([\d.]+)%"),
    "set_per_s": re.compile(r"^set\s*:\s*([\d,]+)/s"),
    "set_success_pct": re.compile(r"^set\s*:.*success\s*:\s*([\d.]+)%"),
    "cache_gets": re.compile(r"^Cache Gets\s*:\s*([\d,]+)"),
    "hit_ratio_pct": re.compile(r"^Hit Ratio\s*:\s*([\d.]+)%"),
    "ram_evictions": re.compile(r"^RAM Evictions\s*:\s*([\d,]+)"),
}


def to_num(s):
    s = s.strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def div(a, b):
    return a / b if a is not None and b else None


def parse(path):
    blocks, cur, summary = [], None, {}
    with open(path, errors="replace") as f:
        for raw in f:
            line = raw.strip()
            for name, pat in SUMMARY_PATTERNS.items():
                m = pat.match(line)
                if m:
                    summary[name] = to_num(m.group(1))
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            if key not in BLOCK_FIELDS:
                continue
            if key == "parsed requests":
                cur = {}
                blocks.append(cur)
            if cur is not None:
                cur[BLOCK_FIELDS[key]] = to_num(val)

    prev_parsed = 0.0
    for b in blocks:
        parsed = b.get("parsed_requests")
        if parsed is not None:
            b["lookups_per_s"] = div(parsed - prev_parsed, b.get("interval_s"))
            prev_parsed = parsed
        scored = b.get("candidates_scored")
        predict, build = b.get("predict_s"), b.get("build_feature_s")
        b["predict_us_per_candidate"] = div(predict and predict * 1e6, scored)
        if predict is not None and build is not None:
            b["model_us_per_candidate"] = div((predict - build) * 1e6, scored)
        used, empty = b.get("evictions_by_ml"), b.get("evictions_queue_empty")
        if used is not None and empty is not None:
            b["evict_queue_empty_frac"] = div(empty, used + empty)
    return blocks, summary


def fmt(v):
    if v is None:
        return "n/a"
    return f"{v:,.4f}" if abs(v) < 1000 else f"{v:,.0f}"


def write(path, blocks, summary):
    cols = list(BLOCK_FIELDS.values()) + DERIVED
    with open(path + ".ml.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["block"] + cols)
        w.writeheader()
        for i, b in enumerate(blocks):
            w.writerow({"block": i, **{c: b.get(c, "") for c in cols}})

    def total(col):
        vals = [b[col] for b in blocks if b.get(col) is not None]
        return sum(vals) if vals else None

    scored, predict, build = (total("candidates_scored"), total("predict_s"),
                              total("build_feature_s"))
    used, empty = total("evictions_by_ml"), total("evictions_queue_empty")
    second_half = blocks[len(blocks) // 2:]
    late_miss = [b["miss_rate"] for b in second_half if b.get("miss_rate") is not None]

    lines = [f"log: {path}", f"stats blocks: {len(blocks)}", "",
             "== cachebench summary =="]
    lines += [f"{k}: {fmt(v)}" for k, v in summary.items()] or ["(not found - run incomplete?)"]
    lines += ["", "== ML totals (summed over all blocks) ==",
              f"candidates scored: {fmt(scored)}",
              f"predict time (s, CPU time summed over prediction threads): {fmt(predict)}",
              f"build feature time (s): {fmt(build)}",
              f"predict us/candidate (features + model + decision): "
              f"{fmt(div(predict and predict * 1e6, scored))}",
              f"model+decision us/candidate (excl. features): "
              f"{fmt(div((predict - build) * 1e6, scored) if predict is not None and build is not None else None)}",
              f"evictions using ML choice: {fmt(used)}",
              f"evictions with empty queue (fell back): {fmt(empty)}",
              f"empty-queue fraction: "
              f"{fmt(div(empty, used + empty) if used is not None and empty is not None else None)}",
              "", "== miss rate per block ==",
              " ".join(fmt(b.get("miss_rate")) for b in blocks) or "n/a",
              f"mean miss rate, second half of run: "
              f"{fmt(sum(late_miss) / len(late_miss)) if late_miss else 'n/a'}"]
    with open(path + ".ml_summary.txt", "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for path in sys.argv[1:]:
        blocks, summary = parse(path)
        write(path, blocks, summary)
        print(f"{path}: {len(blocks)} stats blocks -> {path}.ml.csv, {path}.ml_summary.txt")
        if not blocks:
            print("  no stats blocks found: run shorter than ~10M lookups, "
                  "or debug_mode is 0 in MLConfig")


if __name__ == "__main__":
    main()
