#!/usr/bin/env python3
"""Service provider. Advertises itself to the registry every --interval
seconds and answers simple requests on its own service port."""
import argparse
import signal
import socket
import sys
import threading
import time


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def local_ip_towards(dest_ip):
    """IP of the interface used to reach dest_ip (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((dest_ip, 9))
        return s.getsockname()[0]
    finally:
        s.close()


def serve(port, name, my_ip):
    """The actual 'service': replies to any datagram it receives."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", port))
    while True:
        data, addr = s.recvfrom(2048)
        s.sendto(f"SERVICE_REPLY|{name}|{my_ip}:{port}".encode(), addr)


def main():
    ap = argparse.ArgumentParser(description="UDP service server")
    ap.add_argument("--name", default="web", help="service name to advertise")
    ap.add_argument("--port", type=int, required=True, help="port this service listens on")
    ap.add_argument("--registry-ip", default="10.0.0.1")
    ap.add_argument("--registry-port", type=int, default=5000)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--ip", default=None, help="IP to advertise (auto-detected by default)")
    args = ap.parse_args()

    my_ip = args.ip or local_ip_towards(args.registry_ip)
    registry = (args.registry_ip, args.registry_port)
    adv = f"ADVERTISE|{args.name}|{my_ip}|{args.port}".encode()
    bye = f"DEREGISTER|{args.name}|{my_ip}|{args.port}".encode()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def shutdown(*_):
        sock.sendto(bye, registry)
        log("Deregistered, exiting")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    threading.Thread(target=serve, args=(args.port, args.name, my_ip), daemon=True).start()
    log(f"Server '{args.name}' at {my_ip}:{args.port}, advertising to {registry} every {args.interval}s")

    while True:
        sock.sendto(adv, registry)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
