common = {
        'verbose' : 'debug'
}

core = dict()
core['tx-interval'] = "10"
core['validity-time'] = 30

iface_name = "term00"


# required for operation
core['v4-mcast-addr'] = "224.0.0.1"
core['v4-mcast-port'] = 31001
core['v4-mcast-ttl'] = 10

l0_top_addr_v4 = "10.10.10.228"

# the address of the terminal, e.g. will
# the next hop
l0_bottom_addr_v4 = "10.10.10.1"

# the submitted local interface ip prefix
l0_prefix_v4     = "10.2.101.0"
l0_prefix_len_v4 = "29"

# the initial air address. is later updated by
# quering the interface
l1_top_addr_v4 = "192.166.10.10"
l1_top_iface_name = "air0"


# collected information is forwarded to the
# following instance
update_ipc = dict()
update_ipc['max-update-interval'] = "5"
update_ipc['host'] = "127.0.0.1"
update_ipc['url'] =  "/api/v1/underlay-route-full-dynamic"
update_ipc['content-type'] = "json"
update_ipc['port'] = "16001"


terminal_ipc = dict()
terminal_ipc['url'] = "http://localhost:5180/api/v1/interface"
terminal_ipc['update-interval'] = "20"

