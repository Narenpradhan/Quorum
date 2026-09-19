#!/usr/bin/env python3
"""Quorum High-Throughput Load Testing Suite.

Simulates concurrent virtual users randomly voting across active polls,
evaluating gateway latency, stream queue throughput, and status code distributions.
Supports httpx (asyncio) with standard-library ThreadPoolExecutor fallback.
"""

import argparse
import json
import random
import statistics
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

# Try importing httpx; if not present, standard library fallback is used
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

if not HAS_HTTPX:
    import urllib.error
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor, as_completed


class LoadTestRunner:
    """Executes distributed load tests against the Quorum API Gateway."""

    def __init__(
        self,
        base_url: str,
        total_requests: int,
        concurrency: int,
        virtual_users: int,
        verbose: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.total_requests = total_requests
        self.concurrency = concurrency
        self.virtual_users = virtual_users
        self.verbose = verbose
        self.polls: List[Dict[str, Any]] = []
        self.user_ids = [str(uuid.uuid4()) for _ in range(self.virtual_users)]

    def discover_polls(self) -> bool:
        """Fetch active polls and their available options from the platform."""
        url = f"{self.base_url}/api/polls"
        print(f"[*] Discovering active polls from: {url}")

        try:
            if HAS_HTTPX:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(url)
                    if resp.status_code != 200:
                        print(f"[!] Failed to fetch polls: HTTP {resp.status_code}")
                        return False
                    self.polls = resp.json()
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "Quorum-LoadTest/1.0"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.status != 200:
                        print(f"[!] Failed to fetch polls: HTTP {response.status}")
                        return False
                    data = response.read().decode("utf-8")
                    self.polls = json.loads(data)

            # Filter polls that have options
            self.polls = [p for p in self.polls if p.get("options")]

            if not self.polls:
                print("[!] No active polls with options discovered. Aborting test.")
                return False

            print(f"[✓] Successfully discovered {len(self.polls)} active polls:")
            for p in self.polls:
                opts = [o["label"] for o in p["options"]]
                print(f"    - [Poll {p['id']}] \"{p['title']}\" ({len(opts)} options)")
            return True

        except Exception as exc:
            print(f"[!] Error discovering polls: {exc}")
            return False

    def _prepare_vote_payload(self) -> Tuple[str, Dict[str, Any]]:
        """Generate a random real-world vote payload simulating virtual user behavior."""
        poll = random.choice(self.polls)
        option = random.choice(poll["options"])
        user_base = random.choice(self.user_ids)

        # Enforce unique voter fingerprint per poll to model clean one-vote-per-UID compliance
        voter_fingerprint = f"{user_base[:18]}-{uuid.uuid4().hex[:8]}"

        endpoint = f"/api/polls/{poll['id']}/vote"
        payload = {
            "option_id": option["id"],
            "voter_fingerprint": voter_fingerprint,
        }
        return endpoint, payload

    # -------------------------------------------------------------------------
    # httpx Async Execution Engine
    # -------------------------------------------------------------------------

    async def _run_httpx_async(self) -> List[Dict[str, Any]]:
        import asyncio

        results: List[Dict[str, Any]] = []
        limits = httpx.Limits(
            max_connections=self.concurrency,
            max_keepalive_connections=self.concurrency,
        )

        async with httpx.AsyncClient(limits=limits, timeout=15.0) as client:
            semaphore = asyncio.Semaphore(self.concurrency)
            counter = 0

            async def send_vote(req_index: int) -> Dict[str, Any]:
                nonlocal counter
                endpoint, payload = self._prepare_vote_payload()
                url = f"{self.base_url}{endpoint}"

                async with semaphore:
                    start_time = time.perf_counter()
                    status_code = 0
                    is_success = False
                    error_msg = ""

                    try:
                        resp = await client.post(url, json=payload)
                        latency_ms = (time.perf_counter() - start_time) * 1000
                        status_code = resp.status_code
                        is_success = 200 <= status_code < 300
                    except Exception as exc:
                        latency_ms = (time.perf_counter() - start_time) * 1000
                        error_msg = str(exc)

                    res_entry = {
                        "index": req_index,
                        "endpoint": endpoint,
                        "status_code": status_code,
                        "latency_ms": latency_ms,
                        "success": is_success,
                        "error": error_msg,
                    }

                    status_str = "SUCCESS" if is_success else "FAILED"
                    if self.verbose:
                        code_str = str(status_code) if status_code else "ERR"
                        err_suffix = f" | Error: {error_msg}" if error_msg else ""
                        print(
                            f"[{req_index:04d}] {endpoint} | "
                            f"Status: {status_str} ({code_str}) | "
                            f"Latency: {latency_ms:6.2f}ms{err_suffix}"
                        )

                    return res_entry

            tasks = [send_vote(i + 1) for i in range(self.total_requests)]
            results = await asyncio.gather(*tasks)

        return list(results)

    # -------------------------------------------------------------------------
    # Standard Library Fallback Execution Engine (ThreadPoolExecutor)
    # -------------------------------------------------------------------------

    def _execute_urllib_request(self, req_index: int) -> Dict[str, Any]:
        endpoint, payload = self._prepare_vote_payload()
        url = f"{self.base_url}{endpoint}"
        body_bytes = json.dumps(payload).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Quorum-LoadTest/1.0",
        }
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")

        start_time = time.perf_counter()
        status_code = 0
        is_success = False
        error_msg = ""

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                latency_ms = (time.perf_counter() - start_time) * 1000
                status_code = resp.status
                is_success = 200 <= status_code < 300
        except urllib.error.HTTPError as http_err:
            latency_ms = (time.perf_counter() - start_time) * 1000
            status_code = http_err.code
            is_success = 200 <= status_code < 300
            error_msg = http_err.reason
        except Exception as exc:
            latency_ms = (time.perf_counter() - start_time) * 1000
            error_msg = str(exc)

        status_str = "SUCCESS" if is_success else "FAILED"
        if self.verbose:
            code_str = str(status_code) if status_code else "ERR"
            err_suffix = f" | Error: {error_msg}" if error_msg else ""
            print(
                f"[{req_index:04d}] {endpoint} | "
                f"Status: {status_str} ({code_str}) | "
                f"Latency: {latency_ms:6.2f}ms{err_suffix}"
            )

        return {
            "index": req_index,
            "endpoint": endpoint,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "success": is_success,
            "error": error_msg,
        }

    def _run_urllib_threaded(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = [
                executor.submit(self._execute_urllib_request, i + 1)
                for i in range(self.total_requests)
            ]
            for future in as_completed(futures):
                results.append(future.result())
        return results

    # -------------------------------------------------------------------------
    # Main Benchmark Driver
    # -------------------------------------------------------------------------

    def run(self) -> None:
        """Run the load test and output detailed summary metrics."""
        if not self.discover_polls():
            return

        engine_name = "httpx (asyncio)" if HAS_HTTPX else "standard library (ThreadPoolExecutor)"
        print("\n" + "=" * 60)
        print("                 STARTING LOAD TEST")
        print("=" * 60)
        print(f"Target URL            : {self.base_url}")
        print(f"Total Requests        : {self.total_requests}")
        print(f"Concurrency Level     : {self.concurrency}")
        print(f"Simulated Users       : {self.virtual_users}")
        print(f"Engine                : {engine_name}")
        print("-" * 60)

        overall_start = time.perf_counter()

        if HAS_HTTPX:
            import asyncio
            results = asyncio.run(self._run_httpx_async())
        else:
            results = self._run_urllib_threaded()

        total_time = time.perf_counter() - overall_start

        # Process Metrics
        total_processed = len(results)
        throughput = (total_processed / total_time) if total_time > 0 else 0.0

        status_counts: Dict[int, int] = {}
        successful = 0
        failed = 0
        latencies: List[float] = []

        for r in results:
            code = r["status_code"]
            status_counts[code] = status_counts.get(code, 0) + 1
            if r["success"]:
                successful += 1
            else:
                failed += 1
            latencies.append(r["latency_ms"])

        latencies.sort()
        min_lat = min(latencies) if latencies else 0.0
        max_lat = max(latencies) if latencies else 0.0
        avg_lat = statistics.mean(latencies) if latencies else 0.0
        p50_lat = statistics.median(latencies) if latencies else 0.0
        p95_lat = latencies[int(len(latencies) * 0.95)] if latencies else 0.0
        p99_lat = latencies[int(len(latencies) * 0.99)] if latencies else 0.0

        success_pct = (successful / total_processed * 100) if total_processed else 0.0
        failed_pct = (failed / total_processed * 100) if total_processed else 0.0

        # Output Required Summary Format
        print("\n" + "=" * 60)
        print("               QUORUM LOAD TEST SUMMARY")
        print("=" * 60)
        print(f"Total Requests Processed : {total_processed}")
        print(f"Total Time Taken         : {total_time:.2f} seconds")
        print(f"Throughput Rate          : {throughput:.2f} requests/sec")
        print(f"Status Code Breakdown    : {dict(sorted(status_counts.items()))}")
        print(f"Successful Requests      : {successful} ({success_pct:.1f}%)")
        print(f"Failed Requests          : {failed} ({failed_pct:.1f}%)")
        print("-" * 60)
        print("Latency Breakdown:")
        print(f"  Min Latency            : {min_lat:.2f} ms")
        print(f"  Average Latency        : {avg_lat:.2f} ms")
        print(f"  Median (P50)           : {p50_lat:.2f} ms")
        print(f"  95th Percentile (P95)  : {p95_lat:.2f} ms")
        print(f"  99th Percentile (P99)  : {p99_lat:.2f} ms")
        print(f"  Max Latency            : {max_lat:.2f} ms")
        print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="""
================================================================================
           QUORUM DISTRIBUTED LOAD TESTING & BENCHMARK SUITE
================================================================================
Simulates concurrent virtual users casting votes randomly across active polls,
evaluating the FastAPI Gateway latency, Redis stream queue buffering, and
PostgreSQL asynchronous batch persistence throughput.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage Examples:
  # 1. Standard benchmark with 500 requests across 25 concurrent connections:
  python3 scripts/load_test.py

  # 2. High-throughput stress test with 2,000 requests, 100 concurrency, and 300 virtual users:
  python3 scripts/load_test.py -n 2000 -c 100 -u 300

  # 3. Verbose request-by-request execution against Nginx gateway:
  python3 scripts/load_test.py -n 100 -c 10 -v

  # 4. Target the backend API directly (bypassing Nginx):
  python3 scripts/load_test.py --base-url http://localhost:8000 -n 500 -c 50

Expected Output Format:
  Total Requests Processed : 500
  Total Time Taken         : 0.38 seconds
  Throughput Rate          : 1315.79 requests/sec
  Status Code Breakdown    : {202: 500}
================================================================================
        """,
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=25,
        help="Number of concurrent workers/connections generating load simultaneously (default: 25)",
    )
    parser.add_argument(
        "-n",
        "--requests",
        type=int,
        default=500,
        help="Total number of vote requests to execute during the benchmark run (default: 500)",
    )
    parser.add_argument(
        "-u",
        "--users",
        type=int,
        default=100,
        help="Number of simulated virtual users with unique client identities (default: 100)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:8080",
        help="Base URL of the target Quorum cluster or API gateway (default: http://localhost:8080)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable live stream output for every individual request with latency and status code",
    )

    args = parser.parse_args()

    runner = LoadTestRunner(
        base_url=args.base_url,
        total_requests=args.requests,
        concurrency=args.concurrency,
        virtual_users=args.users,
        verbose=args.verbose,
    )
    runner.run()


if __name__ == "__main__":
    main()
