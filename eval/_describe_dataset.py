"""Summarize the SWE-bench Verified text dataset we're evaluating on."""
import statistics
from collections import Counter
from datasets import load_from_disk

DS = r"C:\Users\fmche\PycharmProjects\clawcodex\SWE-bench-dev\datasets\SWE-bench__SWE-bench_Verified__style-3__fs-oracle"
ds = load_from_disk(DS)["test"]

print(f"Total instances: {len(ds)}")
print(f"Fields per instance: {list(ds[0].keys())}")
print()

# By repo
by_repo = Counter(r["repo"] for r in ds)
print(f"By repo ({len(by_repo)} repos):")
for repo, n in by_repo.most_common():
    print(f"  {repo:30s}  {n}")
print()

# Prompt size
prompt_sizes = [len(r["text"]) for r in ds]
print(f"Prompt size (chars):")
print(f"  min:    {min(prompt_sizes):>8,}")
print(f"  p25:    {int(statistics.quantiles(prompt_sizes, n=4)[0]):>8,}")
print(f"  median: {int(statistics.median(prompt_sizes)):>8,}")
print(f"  p75:    {int(statistics.quantiles(prompt_sizes, n=4)[2]):>8,}")
print(f"  p95:    {int(statistics.quantiles(prompt_sizes, n=20)[18]):>8,}")
print(f"  max:    {max(prompt_sizes):>8,}")
print(f"  total:  {sum(prompt_sizes):>8,}")
print()

# Gold patch sizes
patch_sizes = [len(r["patch"]) for r in ds]
print(f"Gold patch size (chars):")
print(f"  min:    {min(patch_sizes):>8,}")
print(f"  median: {int(statistics.median(patch_sizes)):>8,}")
print(f"  p95:    {int(statistics.quantiles(patch_sizes, n=20)[18]):>8,}")
print(f"  max:    {max(patch_sizes):>8,}")
print()

# Tests per instance
import json
fail_to_pass = [len(json.loads(r["FAIL_TO_PASS"]) if isinstance(r["FAIL_TO_PASS"], str) else r["FAIL_TO_PASS"]) for r in ds]
pass_to_pass = [len(json.loads(r["PASS_TO_PASS"]) if isinstance(r["PASS_TO_PASS"], str) else r["PASS_TO_PASS"]) for r in ds]
print(f"FAIL_TO_PASS tests per instance:")
print(f"  median: {int(statistics.median(fail_to_pass))}  max: {max(fail_to_pass)}")
print(f"PASS_TO_PASS tests per instance:")
print(f"  median: {int(statistics.median(pass_to_pass))}  max: {max(pass_to_pass)}")
print(f"  total tests evaluated across all 499: {sum(fail_to_pass)+sum(pass_to_pass):,}")
print()

# Show one concrete example
print("=" * 60)
print("SAMPLE INSTANCE (astropy__astropy-12907)")
print("=" * 60)
row = next(r for r in ds if r["instance_id"] == "astropy__astropy-12907")
print(f"repo:          {row['repo']}")
print(f"base_commit:   {row['base_commit']}")
print(f"version:       {row.get('version', '(none)')}")
print(f"prompt len:    {len(row['text']):,} chars")
print(f"gold patch len: {len(row['patch']):,} chars")
print()
ftp = json.loads(row["FAIL_TO_PASS"]) if isinstance(row["FAIL_TO_PASS"], str) else row["FAIL_TO_PASS"]
ptp = json.loads(row["PASS_TO_PASS"]) if isinstance(row["PASS_TO_PASS"], str) else row["PASS_TO_PASS"]
print(f"FAIL_TO_PASS ({len(ftp)} tests):")
for t in ftp[:3]:
    print(f"  {t}")
print(f"PASS_TO_PASS ({len(ptp)} tests):")
for t in ptp[:3]:
    print(f"  {t}")
print()
print("--- problem_statement (first 600 chars) ---")
ps = row.get("problem_statement", "")
print(ps[:600] + ("..." if len(ps) > 600 else ""))
print()
print("--- gold patch (first 600 chars) ---")
print(row["patch"][:600] + ("..." if len(row["patch"]) > 600 else ""))
