#!/usr/bin/env python3

import subprocess
import re
import sys
import time

BASE_URL = "http://quorum.local.com"

# Progressive load levels
STAGES = [
    # (requests, concurrency, users)
    (2_000,   100,  300),
    (5_000,   150,  500),
    (10_000,  200,  750),
    (15_000,  250,  1_000),
    (20_000,  300,  1_500),
    (30_000,  400,  2_000),
    (40_000,  500,  3_000),
    (50_000,  600,  4_000),
]

# Stop if failure rate reaches this percentage.
MAX_FAILURE_RATE = 1.0

# Stop if P99 reaches this latency in milliseconds.
MAX_P99_MS = 10_000

# Give the system some recovery time between tests.
COOLDOWN_SECONDS = 30


def run_test(requests, concurrency, users):
    command = [
        sys.executable,
        "load_test.py",
        "--base-url",
        BASE_URL,
        "-n",
        str(requests),
        "-c",
        str(concurrency),
        "-u",
        str(users),
    ]

    print("\n" + "=" * 80)
    print("STARTING LOAD TEST")
    print("=" * 80)
    print(f"Requests      : {requests:,}")
    print(f"Concurrency   : {concurrency:,}")
    print(f"Virtual Users : {users:,}")
    print("=" * 80)

    start = time.time()

    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
    )

    elapsed = time.time() - start

    output = result.stdout + "\n" + result.stderr

    print(output)

    return output, elapsed, result.returncode


def extract_metrics(output):
    metrics = {}

    patterns = {
        "total_requests": r"Total Requests Processed\s*:\s*(\d+)",
        "time": r"Total Time Taken\s*:\s*([\d.]+)",
        "throughput": r"Throughput Rate\s*:\s*([\d.]+)",
        "successful": r"Successful Requests\s*:\s*(\d+)",
        "failed": r"Failed Requests\s*:\s*(\d+)",
        "p50": r"Median \(P50\)\s*:\s*([\d.]+)",
        "p95": r"95th Percentile \(P95\)\s*:\s*([\d.]+)",
        "p99": r"99th Percentile \(P99\)\s*:\s*([\d.]+)",
        "max": r"Max Latency\s*:\s*([\d.]+)",
    }

    for name, pattern in patterns.items():
        match = re.search(pattern, output)
        if match:
            metrics[name] = float(match.group(1))

    return metrics


def should_stop(metrics, expected_requests):
    failed = metrics.get("failed", 0)
    total = metrics.get("total_requests", expected_requests)

    if total:
        failure_rate = (failed / total) * 100
    else:
        failure_rate = 100

    p99 = metrics.get("p99", 0)

    if failure_rate >= MAX_FAILURE_RATE:
        print(
            f"\n[!] STOPPING: failure rate reached "
            f"{failure_rate:.2f}%"
        )
        return True

    if p99 >= MAX_P99_MS:
        print(
            f"\n[!] STOPPING: P99 latency reached "
            f"{p99:.2f} ms"
        )
        return True

    return False


def main():
    print("=" * 80)
    print("QUORUM PROGRESSIVE LOAD / BREAKPOINT TEST")
    print("=" * 80)
    print(f"Target: {BASE_URL}")
    print(f"Maximum failure rate: {MAX_FAILURE_RATE}%")
    print(f"Maximum P99 latency: {MAX_P99_MS:,} ms")
    print("=" * 80)

    results = []

    for stage, (requests, concurrency, users) in enumerate(STAGES, 1):

        print(f"\n\n########## STAGE {stage}/{len(STAGES)} ##########")

        output, elapsed, return_code = run_test(
            requests,
            concurrency,
            users,
        )

        metrics = extract_metrics(output)

        if not metrics:
            print("\n[!] Could not parse test results.")
            print("[!] Stopping to avoid blindly increasing load.")
            break

        total = metrics.get("total_requests", requests)
        failed = metrics.get("failed", 0)

        failure_rate = (
            (failed / total) * 100
            if total
            else 100
        )

        result = {
            "stage": stage,
            "requests": requests,
            "concurrency": concurrency,
            "users": users,
            "throughput": metrics.get("throughput"),
            "p50": metrics.get("p50"),
            "p95": metrics.get("p95"),
            "p99": metrics.get("p99"),
            "max": metrics.get("max"),
            "failed": failed,
            "failure_rate": failure_rate,
        }

        results.append(result)

        print("\n" + "-" * 80)
        print("STAGE RESULT")
        print("-" * 80)
        print(f"Throughput : {metrics.get('throughput', 'N/A')} req/sec")
        print(f"P50        : {metrics.get('p50', 'N/A')} ms")
        print(f"P95        : {metrics.get('p95', 'N/A')} ms")
        print(f"P99        : {metrics.get('p99', 'N/A')} ms")
        print(f"Max        : {metrics.get('max', 'N/A')} ms")
        print(f"Failed     : {failed}")
        print(f"Failure %  : {failure_rate:.2f}%")
        print("-" * 80)

        if return_code != 0:
            print("\n[!] Load-test process exited with an error.")
            print("[!] Stopping.")
            break

        if should_stop(metrics, requests):
            break

        if stage < len(STAGES):
            print(
                f"\n[*] Stage passed. "
                f"Waiting {COOLDOWN_SECONDS}s before increasing load..."
            )
            time.sleep(COOLDOWN_SECONDS)

    print("\n\n" + "=" * 100)
    print("FINAL PROGRESSIVE LOAD TEST SUMMARY")
    print("=" * 100)

    print(
        f"{'Stage':<7}"
        f"{'Req':<10}"
        f"{'Conc':<8}"
        f"{'VUs':<8}"
        f"{'RPS':<12}"
        f"{'P50':<12}"
        f"{'P95':<12}"
        f"{'P99':<12}"
        f"{'Fail %':<10}"
    )

    print("-" * 100)

    for r in results:
        print(
            f"{r['stage']:<7}"
            f"{r['requests']:<10,}"
            f"{r['concurrency']:<8}"
            f"{r['users']:<8}"
            f"{str(r['throughput']):<12}"
            f"{str(r['p50']):<12}"
            f"{str(r['p95']):<12}"
            f"{str(r['p99']):<12}"
            f"{r['failure_rate']:.2f}%"
        )

    print("=" * 100)


if __name__ == "__main__":
    main()
