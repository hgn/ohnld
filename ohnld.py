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
import urllib.request
import urllib.error
import pprint


# don't recognize own mcast transmissions
# by default, can be changed for debugging
MCAST_LOOP = 0

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

    sock.bind(('', int(conf['core']['v4-mcast-port'])))
    host = socket.gethostbyname(socket.gethostname())
    sock.setsockopt(socket.SOL_IP, socket.IP_MULTICAST_IF, socket.inet_aton(host))

    mreq = socket.inet_aton(conf['core']['v4-mcast-addr']) + socket.inet_aton(conf['core']['v4-unicast-addr'])
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    return sock


def init_v4_tx_fd(conf):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, int(conf['core']['v4-mcast-ttl']))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(conf['core']['v4-unicast-addr']))
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


def create_payload_auxiliary_data(conf, db, data):
    data['auxiliary-data'] = {}
    if "terminal-data" in db and 'addr-air-v4' in db["terminal-data"]:
        data['auxiliary-data']['terminal-air-addr-v4'] = db["terminal-data"]['addr-air-v4']


def create_payload_data(conf, db):
    data = {}
    data["cookie"] = SECRET_COOKIE
    create_payload_routing(conf, data)
    create_payload_auxiliary_data(conf, db, data)
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


def create_payload(conf, db):
    payload = create_payload_data(conf, db)
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


async def tx_v4(fd, conf, db):
    addr     = conf['core']['v4-mcast-addr']
    port     = int(conf['core']['v4-mcast-port'])
    interval = float(conf['core']['tx-interval'])
    while True:
        try:
            data = create_payload(conf, db)
            ret = fd.sendto(data, (addr, port))
            emsg = "transmitted OHNDL message via {}:{} of size {}"
            print(emsg.format(addr, port, ret))
        except Exception as e:
            print(str(e))
        await asyncio.sleep(interval)


def db_new():
    db = {}
    # networks we learn from our neighbors via OHNDL
    db["networks"] = list()

    # auxiliary data we learn from our neighbors like
    # air ip address via OHNDL
    db['auxiliary-data'] = dict()

    # terminal data we learn from our terminal
    # directly, this is why this data is stored here
    # and not at conf element
    db['terminal-data'] = dict()
    db['terminal-data']['ipv4-addr-air'] = None
    return db


def db_entry_update(db_entry, data, prefix):
    if db_entry[1]["src-ip"] != data["src-addr"]:
        print("WARNING, seems another router ({}) also announce {}".format(data["src-addr"], prefix))
        db_entry[1]["src-ip"] = data["src-addr"]
    print("route refresh for {} by {}".format(db_entry[0], data["src-addr"]))
    db_entry[1]["last-seen"] = datetime.datetime.utcnow()


def db_entry_new(conf, db, data, prefix):
    entry = []
    entry.append(prefix)

    second_element = {}
    second_element["src-ip"] = data["src-addr"]
    second_element["last-seen"] = datetime.datetime.utcnow()
    entry.append(second_element)

    db["networks"].append(entry)
    print("new route announcement for {} by {}".format(prefix, data["src-addr"]))


def save_auxiliary_data(db, data):
    src_ip = data["src-addr"]
    if not 'auxiliary-data' in data['payload']:
        print("no auxiliary-data section in received OHNDL packet")
        return
    db['auxiliary-data'][src_ip] = data['payload']['auxiliary-data']
    print("save auxiliary data from {}:".format(src_ip))
    print("  {}".format(data['payload']['auxiliary-data']))


def update_db(conf, db, data):
    new_entry = False

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
            db_entry_new(conf, db, data, prefix)
            new_entry = True

    save_auxiliary_data(db, data)

    if new_entry:
        ipc_trigger_update_routes(conf, db)


async def handle_packet(queue, conf, db):
    while True:
        entry = await queue.get()
        data = parse_payload(entry)
        if data:
            update_db(conf, db, data)


async def db_check_outdated(db, conf):
    while True:
        entry_outdated = False
        for db_entry in db["networks"]:
            last_seen_time = db_entry[1]["last-seen"]
            now = datetime.datetime.utcnow()
            diff_sec = (now - last_seen_time).total_seconds()
            if diff_sec > float(conf["core"]["validity-time"]):
                print("route entry outdated: {}".format(db_entry))
                db["networks"].remove(db_entry)
                entry_outdated = True
        if entry_outdated:
            ipc_trigger_update_routes(conf, db)
        await asyncio.sleep(1)


def query_interface_data(db, conf):
    url = conf["terminal-ipc"]["url"]
    user_agent_headers = { 'Content-type': 'application/json',
                           'Accept':       'application/json' }
    proxy_support = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_support)
    urllib.request.install_opener(opener)

    data = dict()
    tx_data = json.dumps(data).encode('utf-8')
    request = urllib.request.Request(url, tx_data, user_agent_headers)
    try:
        server_response = urllib.request.urlopen(request).read()
    except urllib.error.HTTPError as e:
        print("Failed to reach the route-manager ({}): '{}'".format(url, e.reason))
        return None
    except urllib.error.URLError as e:
        print("Failed to reach the route-manager ({}): '{}'".format(url, e.reason))
        return None
    server_data = json.loads(str(server_response, "utf-8"))
    print("Answer IPC:")
    print(server_data)
    return server_data


def check_interface_data(db, conf):
    data = query_interface_data(db, conf)
    if data == None:
        return
    if type(data) is not str:
        raise Exception("ipv4 terminal air addr must be string")
    db['terminal-data']['ipv4-addr-air'] = addr



async def terminal_check_interface(db, conf):
    update_interval = int(conf['terminal-ipc']['update-interval'])
    while True:
        check_interface_data(db, conf)
        await asyncio.sleep(update_interval)


def ipc_send_request(conf, data = None):
    url = "http://{}:{}{}".format(conf["update-ipc"]["host"],
            conf["update-ipc"]["port"], conf["update-ipc"]["url"])
    user_agent_headers = { 'Content-type': 'application/json',
                           'Accept':       'application/json' }

    # just ignore any configured system proxy, we don't need
    # a proxy for localhost communication
    proxy_support = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_support)
    urllib.request.install_opener(opener)

    request = urllib.request.Request(url, data.encode('ascii'), user_agent_headers)
    try:
        server_response = urllib.request.urlopen(request).read()
    except urllib.error.HTTPError as e:
        print("Failed to reach the route-manager ({}): '{}'".format(url, e.reason))
        return False, e.reason
    except urllib.error.URLError as e:
        print("Failed to reach the route-manager ({}): '{}'".format(url, e.reason))
        return False, e.reason
    server_data = json.loads(str(server_response, "utf-8"))
    if server_data['status'] != "ok":
        return False, server_data
    return True, None


def ipc_trigger_update_routes(conf, db):
    """ called when we receive new information from
        our nieghbors """
    cmd = {}
    # this is the local terminal which received infos from
    # other terminals via OHNDL.
    cmd["terminal"] = {}
    cmd["terminal"]["iface-name"] = conf["core"]["iface-name"]
    cmd["terminal"]["ip-eth-v4"] = conf["core"]["terminal-v4-addr"]

    cmd["routes"] = []

    for db_entry in db["networks"]:
        prefix, prefix_len = db_entry[0].split("/")
        e = {}
        e["l2-proto"] = "IPv4"
        e["prefix"]     = prefix
        e["prefix-len"] = prefix_len
        e['originator-ohndl-addr-v4'] = db_entry[1]["src-ip"]
        cmd["routes"].append(e)

    cmd['terminal-air-ip-list'] = list()
    for k, v in db['auxiliary-data'].items():
        d = dict()
        ip_router_addr = k
        ip_terminal_air = v['terminal-air-addr-v4']
        d['router-addr-v4'] = ip_router_addr
        d['terminal-air-addr-v4'] = ip_terminal_air
        cmd['terminal-air-ip-list'].append(d)

    pprint.pprint(cmd)

    cmd_json = json.dumps(cmd)
    ok, error = ipc_send_request(conf, cmd_json)
    if ok: print("updated sucessfully route-manager")


async def ipc_regular_update(db, conf):
    while True:
        await asyncio.sleep(float(conf["update-ipc"]["max-update-interval"]))
        ipc_trigger_update_routes(conf, db)

def ask_exit(signame, loop):
    sys.stderr.write("got signal %s: exit\n" % signame)
    loop.stop()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-f", "--configuration", help="configuration", type=str, default=None)
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


def db_set_configuration_values(db, conf):
    if not "terminal-data" in conf:
        return
    if not "addr-air-v4" in conf['terminal-data']:
        return
    db["terminal-data"]['addr-air-v4'] = conf['terminal-data']['addr-air-v4']


def main():
    sys.stderr.write("OHNLD - 2016,2017\n")
    conf = conf_init()
    db = db_new()
    db_set_configuration_values(db, conf)

    loop = asyncio.get_event_loop()
    queue = asyncio.Queue(32)

    # RX functionality
    fd = init_v4_rx_fd(conf)
    loop.add_reader(fd, functools.partial(cb_v4_rx, fd, queue))

    # TX side
    fd = init_v4_tx_fd(conf)
    asyncio.ensure_future(tx_v4(fd, conf, db))

    # Outputter
    asyncio.ensure_future(handle_packet(queue, conf, db))

    # we regulary transmit
    asyncio.ensure_future(ipc_regular_update(db, conf))

    # just call a function every n seconds to check for outdated
    # elements, reduce CPU load instead of add an callback to
    # every DB entry, which will be more exact - which is not required
    asyncio.ensure_future(db_check_outdated(db, conf))

    # read terminal ip address
    #asyncio.ensure_future(terminal_check_interface(db, conf))


    for signame in ('SIGINT', 'SIGTERM'):
        loop.add_signal_handler(getattr(signal, signame),
                                functools.partial(ask_exit, signame, loop))

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
