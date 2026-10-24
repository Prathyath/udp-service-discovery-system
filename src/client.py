#!/usr/bin/env python3
"""Discovery client. Sends DISCOVER to the registry, retries on timeout,
and measures discovery latency."""
import argparse
import csv
import os
import socket
import statistics
import time


def discover(sock, registry, service, timeout, retries):
    """Returns (servers, latency_ms, attempts). servers is None if all attempts timed out."""
    msg = f"DISCOVER|{service}".encode()
    sock.settimeout(timeout)
    for attempt in range(1, retries + 1):
        start = time.perf_counter()
        sock.sendto(msg, registry)
        try:
            data, _ = sock.recvfrom(2048)
        except socket.timeout:
            continue
        latency = (time.perf_counter() - start) * 1000
        parts = data.decode().split("|")
        if len(parts) == 3 and parts[0] == "RESPONSE" and parts[1] == service:
            servers = [s for s in parts[2].split(",") if s]
            return servers, latency, attempt
    return None, None, retries


def main():
    ap = argparse.ArgumentParser(description="UDP discovery client")
    ap.add_argument("--service", default="web")
    ap.add_argument("--registry-ip", default="10.0.0.1")
    ap.add_argument("--registry-port", type=int, default=5000)
    ap.add_argument("--timeout", type=float, default=1.0)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--count", type=int, default=1, help="number of discovery queries")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between queries")
    ap.add_argument("--csv", default=None, help="append results to this CSV file")
    ap.add_argument("--label", default="", help="scenario label stored in the CSV")
    ap.add_argument("--use", action="store_true", help="also send a request to the first discovered server")
    args = ap.parse_args()

    registry = (args.registry_ip, args.registry_port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    latencies, failures = [], 0
    rows = []

    for i in range(args.count):
        servers, lat, attempts = discover(sock, registry, args.service, args.timeout, args.retries)
        if servers is None:
            failures += 1
            print(f"[{i+1}] FAILED: no response from registry after {args.retries} attempts")
            rows.append([time.time(), args.label, args.service, attempts, "", 0, "timeout"])
        else:
            latencies.append(lat)
            print(f"[{i+1}] {len(servers)} server(s) {servers} | latency {lat:.2f} ms | attempts {attempts}")
            rows.append([time.time(), args.label, args.service, attempts, f"{lat:.3f}", len(servers), "ok"])
            if args.use and servers and i == 0:
                ip, port = servers[0].split(":")
                sock.sendto(b"REQUEST", (ip, int(port)))
                try:
                    reply, _ = sock.recvfrom(2048)
                    print(f"     service replied: {reply.decode()}")
                except socket.timeout:
                    print("     service did not reply")
        if i < args.count - 1:
            time.sleep(args.delay)

    if latencies:
        print(f"\nSummary: {len(latencies)}/{args.count} ok | "
              f"avg {statistics.mean(latencies):.2f} ms | min {min(latencies):.2f} | max {max(latencies):.2f}")
    if args.csv:
        new = not os.path.exists(args.csv)
        with open(args.csv, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["timestamp", "label", "service", "attempts", "latency_ms", "num_servers", "status"])
            w.writerows(rows)


if __name__ == "__main__":
    main()
