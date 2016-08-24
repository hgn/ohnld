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
import datetime


# don't recognize own mcast transmissions
# by default, can be changed for debugging
MCAST_LOOP = 0

# For communication with separated thread (e.g. to use dbus-glib)
# janus queue can be used: https://pypi.python.org/pypi/janus

# ident to drop all non-RouTinG applications.
IDENT = "RTG".encode('ascii')

# identify this sender
SECRET_COOKIE = str(uuid.uuid4())

# data compression level
ZIP_COMPRESSION_LEVEL = 9


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
    d = {}
    d["proto"] = "IPv4"
    d["src-addr"]  = addr[0]
    d["src-port"]  = addr[1]
    d["data"]  = data
    try:
        queue.put_nowait(d)
    except asyncio.queues.QueueFull:
        sys.stderr.write("queue overflow, strange things happens")

def create_payload_routing(conf, data):
    if "network-announcement" in conf:
        data["hna"] = conf["network-announcement"]

def create_payload_data(conf):
    data = {}
    data["cookie"] = SECRET_COOKIE
    create_payload_routing(conf, data)
    json_data = json.dumps(data)
    byte_stream = str.encode(json_data)
    compressed = zlib.compress(byte_stream, ZIP_COMPRESSION_LEVEL)
    #print("compression stats: before {} byte - after compression {} byte".format(len(byte_stream), len(compressed)))
    return compressed


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


def parse_payload_header(raw):
    if len(raw) < len(IDENT) + 4:
        # check for minimal length
        # ident(3) + size(>=4) + payload(>=1)
        print("Header to short")
        return False
    ident = raw[0:3]
    if ident != IDENT:
        print("ident wrong: expect:{} received:{}".format(IDENT, ident))
        return False
    return True


def parse_payload_data(raw):
    size = struct.unpack('>I', raw[3:7])[0]
    if len(raw) < 7 + size:
        print("message seems corrupt")
        return False, None
    data = raw[7:7 + size]
    uncompressed_json = str(zlib.decompress(data), "utf-8")
    data = json.loads(uncompressed_json)
    return True, data


def self_check(data):
    if data["cookie"] == SECRET_COOKIE:
        return True
    return False


def parse_payload(packet):
    ok = parse_payload_header(packet["data"])
    if not ok: return

    ok, data = parse_payload_data(packet["data"])
    if not ok: return

    self = self_check(data)
    if self: return

    ret = {}
    ret["src-addr"] = packet["src-addr"]
    ret["src-port"] = packet["src-port"]
    ret["payload"] = data
    return ret

async def tx_v4(fd, conf):
    addr     = conf['core']['v4-addr']
    port     = int(conf['core']['v4-port'])
    interval = float(conf['core']['tx-interval'])
    while True:
        try:
            data = create_payload(conf)
            fd.sendto(data, (addr, port))
        except Exception as e:
            print(str(e))
        await asyncio.sleep(interval)

def db_new():
    db = {}
    db["networks"] = []
    return db

def db_entry_update(db_entry, data, prefix):
    if db_entry[1]["src-ip"] != data["src-addr"]:
        print("WARNING, seems another router ({}) also announce {}".format(data["src-addr"], prefix))
        db_entry[1]["src-ip"] = data["src-addr"]
    print("route refresh for {} by {}".format(db_entry[0], data["src-addr"]))
    db_entry[1]["last-seen"] = datetime.datetime.utcnow()


def db_entry_new(db, data, prefix):
    entry = []
    entry.append(prefix)

    second_element = {}
    second_element["src-ip"] = data["src-addr"]
    second_element["last-seen"] = datetime.datetime.utcnow()
    entry.append(second_element)

    db["networks"].append(entry)
    print("new route announcement for {} by {}".format(prefix, data["src-addr"]))


def update_db(db, data):
    if "hna" not in data["payload"]:
        print("no HNA data in payload, ignoring it")
        return

    for entry in data["payload"]["hna"]:
        found = False
        prefix = "{}/{}".format(entry[0], entry[1])
        for db_entry in db["networks"]:
            if prefix == db_entry[0]:
                db_entry_update(db_entry, data, prefix)
                found = True
        if not found:
            db_entry_new(db, data, prefix)


async def handle_packet(queue, conf, db):
    while True:
        entry = await queue.get()
        data = parse_payload(entry)
        if data:
            update_db(db, data)

async def db_check_outdated(db, conf):
    while True:
        for db_entry in db["networks"]:
            last_seen_time = db_entry[1]["last-seen"]
            now = datetime.datetime.utcnow()
            diff_sec = (now - last_seen_time).total_seconds()
            if diff_sec > float(conf["core"]["validity-time"]):
                print("route entry outdated: {}".format(db_entry))
                db["networks"].remove(db_entry)

        await asyncio.sleep(1)



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
    db = db_new()

    loop = asyncio.get_event_loop()
    queue = asyncio.Queue(32)

    # RX functionality
    fd = init_v4_rx_fd(conf)
    loop.add_reader(fd, functools.partial(cb_v4_rx, fd, queue))

    # TX side
    fd = init_v4_tx_fd(conf)
    asyncio.ensure_future(tx_v4(fd, conf))

    # Outputter
    asyncio.ensure_future(handle_packet(queue, conf, db))

    # just call a function every n seconds to check for outdated
    # elements, reduce CPU load instead of add an callback to
    # every DB entry, which will be more exact - which is not required
    loop.run_until_complete(db_check_outdated(db, conf))

    for signame in ('SIGINT', 'SIGTERM'):
        loop.add_signal_handler(getattr(signal, signame),
                                functools.partial(ask_exit, signame, loop))

    try:
        loop.run_forever()
    finally:
        loop.close()


if __name__ == "__main__":
    main()
