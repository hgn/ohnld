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
import json
import zlib


# don't recognize own mcast transmissions
# by default, can be changed for debugging
MCAST_LOOP = 0

# For communication with separated thread (e.g. to use dbus-glib)
# janus queue can be used: https://pypi.python.org/pypi/janus

# ident to drop all non-RouTinG applications.
IDENT = "RTG".encode('ascii')

SECRET_COOKIE = str.encode(str(uuid.uuid4()))


def init_v4_rx_fd(conf):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(sock, "SO_REUSEPORT"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, MCAST_LOOP)
    
    sock.bind(('', int(conf['core']['v4-port'])))
    host = socket.gethostbyname(socket.gethostname())
    sock.setsockopt(socket.SOL_IP, socket.IP_MULTICAST_IF, socket.inet_aton(host))

    mreq = struct.pack("4sl", socket.inet_aton(conf['core']['v4-addr']), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    return sock


def init_v4_tx_fd(conf):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, int(conf['core']['v4-ttl']))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(conf['core']['v4-out-addr']))
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
    print("Message from: {}:{}".format(str(addr[0]), str(addr[1])))
    print("Message: {!r}".format(data.decode()))
    try:
        queue.put_nowait(d)
    except asyncio.queues.QueueFull:
        sys.stderr.write("queue overflow, strange things happens")


def create_payload_data(conf):
    return "xxxx".encode('ascii')

def create_payload_header(data_len):
    ident = IDENT
    assert len(IDENT) == 3
    data = SECRET_COOKIE
    head = struct.pack('>I', data_len)
    return ident + head

def create_payload(conf):
    payload = create_payload_data(conf)
    header = create_payload_header(len(payload))
    return header + payload


def parse_payload(raw):
    if len(raw) < len(IDENT) + 4:
        # check for minimal length
        # ident(3) + size(>=4) + payload(>=1)
        print("Header to short")
        return False
    ident = raw[0:3]
    if ident != IDENT:
        #print("ident wrong: expect:{} received:{}".format(IDENT, ident))
        return False

    # ok, packet seems to come from mcast-discovery-daemon
    size = struct.unpack('>I', raw[3:7])[0]
    print("size:")
    print(size)
    return True
    cookie = raw[7:]
    #print(size)
    #print("secret cookie: own:{} received:{}".format(SECRET_COOKIE, cookie))
    if cookie == SECRET_COOKIE:
        # own packet, ignore it
        #print("own packet, ignore it")
        return True, True, None, None

    return True, False, None, cookie


async def tx_v4(fd, conf):
    addr     = conf['core']['v4-addr']
    port     = int(conf['core']['v4-port'])
    interval = float(conf['core']['tx-interval'])
    while True:
        try:
            data = create_payload(conf)
            fd.sendto(data, (addr, port))
            print("TX")
        except Exception as e:
            print(str(e))
        await asyncio.sleep(interval)


async def print_stats(queue):
    while True:
        entry = await queue.get()
        data = entry[1]
        parse_payload(data)


def ask_exit(signame, loop):
    sys.stderr.write("got signal %s: exit\n" % signame)
    loop.stop()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--configuration", help="configuration", type=str, default=None)
    args = parser.parse_args()
    if not args.configuration:
        print("Configuration required, please specify a valid file path, exiting now")
        sys.exit(1)
    return args


def load_configuration_file(args):
    with open(args.configuration) as json_data:
        return json.load(json_data)


def conf_init():
    args = parse_args()
    return load_configuration_file(args)


def main():
    conf = conf_init()

    loop = asyncio.get_event_loop()
    queue = asyncio.Queue(32)

    # RX functionality
    fd = init_v4_rx_fd(conf)
    loop.add_reader(fd, functools.partial(cb_v4_rx, fd, queue))

    # TX side
    fd = init_v4_tx_fd(conf)
    asyncio.ensure_future(tx_v4(fd, conf))

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
