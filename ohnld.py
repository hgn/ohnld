#!/usr/bin/python3
# -*- coding: utf-8 -*- 

import asyncio
import socket
import struct
import binascii
import time
import sys
import functools
import argparse
import signal
import os
import uuid


MCAST_ADDR_V4 = '224.0.1.0' 
DEFAULT_PORT  = 31001
DEFAULT_INTERVAL = 5.0
DEFAULT_TTL = 10

# don't recognize own mcast transmissions
# by default, can be changed for debugging
MCAST_LOOP = 0

# For communication with separated thread (e.g. to use dbus-glib)
# janus queue can be used: https://pypi.python.org/pypi/janus

# ident to drop all non-mcast-discovery-daemon applications.
IDENT = "FOO".encode('ascii')

SECRET_COOKIE = str.encode(str(uuid.uuid4()))


def init_v4_rx_fd(addr=None, port=DEFAULT_PORT):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(sock, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, MCAST_LOOP)
    
    sock.bind(('', port))
    host = socket.gethostbyname(socket.gethostname())
    sock.setsockopt(socket.SOL_IP, socket.IP_MULTICAST_IF, socket.inet_aton(host))

    mreq = struct.pack("4sl", socket.inet_aton(addr), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    return sock


def init_v4_tx_fd(ttl, addr=None):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
    if addr:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(addr))
    return sock


def cb_v4_rx(fd, queue):
    try:
        data, addr = fd.recvfrom(1024)
    except socket.error as e:
        print('Expection')
    d = []
    d.append("IPv4")
    d.append(data)
    d.append(addr)
    #print("Messagr from: {}:{}".format(str(addr[0]), str(addr[1])))
    #print("Message: {!r}".format(data.decode()))
    try:
        queue.put_nowait(d)
    except asyncio.queues.QueueFull:
        sys.stderr.write("queue overflow, strange things happens")


def create_payload():
    ident = IDENT
    assert len(IDENT) == 3
    data = SECRET_COOKIE
    data_len = len(data)
    head = struct.pack('>I', data_len)
    return ident + head + data

def parse_payload(raw):
    if len(raw) < 3 + 1 + 1:
        # check for minimal length
        # ident(3) + size(>=1) + payload(>=1)
        return False, None, None, None
    ident = raw[0:3]
    if ident != IDENT:
        #print("ident wrong: expect:{} received:{}".format(IDENT, ident))
        return False, None, None, None

    # ok, packet seems to come from mcast-discovery-daemon
    size = struct.unpack('>I', raw[3:7])[0]
    cookie = raw[7:]
    #print(size)
    #print("secret cookie: own:{} received:{}".format(SECRET_COOKIE, cookie))
    if cookie == SECRET_COOKIE:
        # own packet, ignore it
        #print("own packet, ignore it")
        return True, True, None, None

    return True, False, None, cookie


async def tx_v4(fd, addr=None, port=DEFAULT_PORT, interval=None):
    while True:
        try:
            data = create_payload()
            fd.sendto(data, (addr, port))
        except Exception as e:
            print(str(e))
        await asyncio.sleep(interval)



def update_db(dbx, message_ok, msg, raw_msg, cookie):
    if not message_ok:
        dbx['stats']['packets-corrupt'] += 1
        return
    if cookie not in dbx['stats']['neighbors']:
        dbx['stats']['neighbors'][cookie] = True
    dbx['stats']['packets-received'] += 1
    dbx['stats']['bytes-received'] += len(raw_msg)
    proto = msg[0]
    ip_src_addr = msg[2][0]
    ip_src_port = msg[2][1]

    db = dbx['data']
    if ip_src_addr not in db:
        db[ip_src_addr] = dict()
        db[ip_src_addr]['network-protocol'] = msg[0]
        db[ip_src_addr]['first-seen'] = time.time()
        db[ip_src_addr]['received-messages'] = 0
    db[ip_src_addr]['last-seen'] = time.time()
    db[ip_src_addr]['received-messages'] += 1
    db[ip_src_addr]['source-ports'] = ip_src_port


def display_time(seconds, granularity=2):
    result = []
    intervals = (
            ('weeks', 604800),
            ('days',   86400),
            ('hours',   3600),
            ('minutes',   60),
            ('seconds',    1),
    )

    if seconds <= 1.0:
        return "just now"

    for name, count in intervals:
        value = seconds // count
        if value:
            seconds -= value * count
            if value == 1:
                name = name.rstrip('s')
            result.append("{} {} ".format(value, name))
    return ', '.join(result[:granularity]) + " ago"


def print_db(db):
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    
    if not sys.stdout.isatty():
        HEADER = OKBLUE = OKGREEN = WARNING = FAIL = ENDC = ''

    print("\033c")
    sys.stdout.write("{}Number of Neighbors: {}{} (include outdated neighors)\n".format(WARNING, len(db['stats']['neighbors']), ENDC))
    sys.stdout.write("Received: packets:{}, byte:{}, corrupt:{}\n\n".format(
                     db['stats']['packets-received'], db['stats']['bytes-received'], db['stats']['packets-corrupt']))
    for key, value in db['data'].items():
        sys.stdout.write("{}{}{}\n".format(OKGREEN, key, ENDC))
        now = time.time()
        last_seen_delta = display_time(now - value['last-seen'])
        fist_seen_delta = display_time(now - value['first-seen'])
        sys.stdout.write("\tLast seen:  {}\n".format(last_seen_delta))
        sys.stdout.write("\tFirst seen: {}\n".format(fist_seen_delta))
        sys.stdout.write("\tSource port: {}\n".format(value['source-ports']))
        sys.stdout.write("\tReceived messages: {}\n".format(value['received-messages']))
        print("\n")

def init_stats_db():
    stats = dict()
    stats['packets-received'] = 0
    stats['bytes-received'] = 0
    stats['packets-corrupt'] = 0
    stats['neighbors'] = dict()
    return stats


async def print_stats(queue):
    db = dict()
    db['data'] = dict()
    db['stats'] = init_stats_db()
    while True:
        entry = await queue.get()
        data = entry[1]
        ok, own, parsed_data, cookie = parse_payload(data)
        if not own:
            update_db(db, ok, entry, data, cookie)
        print_db(db)


def ask_exit(signame, loop):
    sys.stderr.write("got signal %s: exit\n" % signame)
    loop.stop()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--v4addr", help="IPv4 mcast address (default: {})".format(MCAST_ADDR_V4), type=str, default=MCAST_ADDR_V4)
    parser.add_argument("--v4outaddr", help="IPv4 outgoing interface address (default: {})".format("None"), type=str, default=None)
    parser.add_argument("--port", help="TX/RX port (default: {})".format(DEFAULT_PORT), type=int, default=DEFAULT_PORT)
    parser.add_argument("--ttl", help="IPv46 TTL for transmission (default: {})".format(DEFAULT_TTL), type=int, default=DEFAULT_TTL)
    parser.add_argument("-i", "--interval", help="Time between transmission (default: {})".format(DEFAULT_INTERVAL), type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("-v", "--verbose", help="increase output verbosity", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()

    loop = asyncio.get_event_loop()
    queue = asyncio.Queue(32)

    # RX functionality
    fd = init_v4_rx_fd(addr=args.v4addr, port=args.port)
    loop.add_reader(fd, functools.partial(cb_v4_rx, fd, queue))

    # TX side
    fd = init_v4_tx_fd(ttl=args.ttl, addr=args.v4outaddr)
    asyncio.ensure_future(tx_v4(fd, addr=args.v4addr, port=args.port, interval=args.interval))

    # Outputter
    asyncio.ensure_future(print_stats(queue))

    for signame in ('SIGINT', 'SIGTERM'):
        loop.add_signal_handler(getattr(signal, signame),
                                functools.partial(ask_exit, signame, loop))

    try:
        loop.run_forever()
    finally:
        loop.close()


if __name__ == "__main__":
    main()
