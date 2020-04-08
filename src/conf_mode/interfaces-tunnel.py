#!/usr/bin/env python3
#
# Copyright (C) 2019 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import netifaces

from sys import exit
from copy import deepcopy

from vyos.config import Config
from vyos.ifconfig import Interface, GREIf, GRETapIf, IPIPIf, IP6GREIf, IPIP6If, IP6IP6If, SitIf, Sit6RDIf
from vyos.ifconfig.afi import IP4, IP6
from vyos.configdict import list_diff
from vyos.validate import is_ipv4, is_ipv6
from vyos import ConfigError


class FixedDict(dict):
    def __init__ (self, **options):
        self._allowed = options.keys()
        super().__init__(**options)

    def __setitem__ (self, k, v):
        if k not in self._allowed:
            raise ConfigError(f'Option "{k}" has no defined default')
        super().__setitem__(k, v)


class ConfigurationState (Config):
    def __init__ (self, section, default):
        super().__init__()
        self.section = section
        self.default = deepcopy(default)
        self.options = FixedDict(**default)
        self.actions = {
            'create': [],  # the key did not exist and was added
            'static': [],  # the key exists and its value was not modfied
            'modify': [],  # the key exists and its value was modified
            'absent': [],  # the key is not present
            'delete': [],  # the key was present and was deleted
        }
        self.changes = {}
        if not self.exists(section):
            self.changes['section'] = 'delete'
        elif self.exists_effective(section):
            self.changes['section'] = 'modify'
        else:
            self.changes['section'] = 'create'

    def _act(self, section):
        if self.exists(section):
            if self.exists_effective(section):
                if self.return_value(section) != self.return_effective_value(section):
                    return 'modify'
                return 'static'
            return 'create'
        else:
            if self.exists_effective(section):
                return 'delete'
            return 'absent'

    def _action (self, name, key):
        action = self._act(key)
        self.changes[name] = action
        self.actions[action].append(name)
        return action

    def _get(self, name, key, default, getter):
        value = getter(key)
        if not value:
            if default:
                self.options[name] = default
                return
            self.options[name] = self.default[name]
            return
        self.options[name] = value

    def get_value(self, name, key, default=None):
        if self._action(name, key) in ('delete', 'absent'):
            return
        return self._get(name, key, default, self.return_value)

    def get_values(self, name, key, default=None):
        if self._action(name, key) in ('delete', 'absent'):
            return
        return self._get(name, key, default, self.return_values)

    def get_effective(self, name, key, default=None):
        self._action(name, key)
        return self._get(name, key, default, self.return_effective_value)

    def get_effectives(self, name, key, default=None):
        self._action(name, key)
        return self._get(name, key, default, self.return_effectives_value)

    def load(self, mapping):
        for local_name, (config_name, multiple, default) in mapping.items():
            if multiple:
                self.get_values(local_name, config_name, default)
            else:
                self.get_value(local_name, config_name, default)

    def remove_default (self,*options):
        for option in options:
            if self.exists(option) and self_return_value(option) != self.default[option]:
                continue
            del self.options[option]

    def to_dict (self):
        # as we have to use a dict() for the API for verify and apply the options
        return {
            'options': self.options,
            'changes': self.changes,
            'actions': self.actions,
        }

default_config_data = {
    # interface definition
    'vrf': '',
    'addresses-add': [],
    'addresses-del': [],
    'state': 'up',
    'dhcp-interface': '',
    'link_detect': 1,
    'ip': False,
    'ipv6': False,
    'nhrp': [],
    'ipv6_autoconf': 0,
    'ipv6_forwarding': 1,
    'ipv6_dad_transmits': 1,
    # internal
    'tunnel': {},
    # the following names are exactly matching the name
    # for the ip command and must not be changed
    'ifname': '',
    'type': '',
    'alias': '',
    'mtu': '1476',
    'local': '',
    'remote': '',
    'multicast': 'disable',
    'allmulticast': 'disable',
    'ttl': '255',
    'tos': 'inherit',
    'key': '',
    'encaplimit': '4',
    'flowlabel': 'inherit',
    'hoplimit': '64',
    'tclass': 'inherit',
    '6rd-prefix': '',
    '6rd-relay-prefix': '',
}

# dict name -> config name, multiple values, default
mapping = {
    'type':                ('encapsulation', False, None),
    'alias':               ('description', False, None),
    'mtu':                 ('mtu', False, None),
    'local':               ('local-ip', False, None),
    'remote':              ('remote-ip', False, None),
    'multicast':           ('multicast', False, None),
    'ttl':                 ('parameters ip ttl', False, None),
    'tos':                 ('parameters ip tos', False, None),
    'key':                 ('parameters ip key', False, None),
    'encaplimit':          ('parameters ipv6 encaplimit', False, None),
    'flowlabel':           ('parameters ipv6 flowlabel', False, None),
    'hoplimit':            ('parameters ipv6 hoplimit', False, None),
    'tclass':              ('parameters ipv6 tclass', False, None),
    '6rd-prefix':          ('6rd-prefix', False, None),
    '6rd-relay-prefix':    ('6rd-relay-prefix', False, None),
    'dhcp-interface':      ('dhcp-interface', False, None),
    'state':               ('disable', False, 'down'),
    'link_detect':         ('disable-link-detect', False, 2),
    'vrf':                 ('vrf', False, None),
    'addresses-add':       ('address', True, None),
    'ipv6_autoconf':       ('ipv6 address autoconf', False, 1),
    'ipv6_forwarding':     ('ipv6 disable-forwarding', False, 0),
    'ipv6_dad_transmits:': ('ipv6 dup-addr-detect-transmits', False, None)
}

def get_class (options):
    dispatch = {
        'gre': GREIf,
        'gre-bridge': GRETapIf,
        'ipip': IPIPIf,
        'ipip6': IPIP6If,
        'ip6ip6': IP6IP6If,
        'ip6gre': IP6GREIf,
        'sit': SitIf,
    }

    kls = dispatch[options['type']]
    if options['type'] == 'gre' and not options['remote'] \
        and not options['key'] and not options['multicast']:
        # will use GreTapIf on GreIf deletion but it does not matter
        return GRETapIf
    elif options['type'] == 'sit' and options['6rd-prefix']:
        # will use SitIf on Sit6RDIf deletion but it does not matter
        return Sit6RDIf
    return kls

def get_interface_ip (ifname):
    if not ifname:
        return ''
    try:
        addrs = Interface(ifname).get_addr()
        if addrs:
            return addrs[0].split('/')[0]
    except Exception:
        return ''

def get_afi (ip):
    return IP6 if is_ipv6(ip) else IP4

def ip_proto (afi):
    return 6 if afi == IP6 else 4


def get_config():
    ifname = os.environ.get('VYOS_TAGNODE_VALUE','')
    if not ifname:
        raise ConfigError('Interface not specified')

    conf = ConfigurationState('interfaces tunnel ' + ifname, default_config_data)
    options = conf.options
    changes = conf.changes
    options['ifname'] = ifname

    # set new configuration level
    conf.set_level(conf.section)

    if changes['section'] == 'delete':
        conf.get_effective('type', mapping['type'][0])
        conf.set_level('protocols nhrp tunnel')
        options['nhrp'] = conf.list_nodes('')
        return conf.to_dict()

    # load all the configuration option according to the mapping
    conf.load(mapping)

    # remove default value if not set and not required
    afi_local = get_afi(options['local'])
    if afi_local == IP6:
        conf.remove_default('ttl', 'tos', 'key')
    if afi_local == IP4:
        conf.remove_default('encaplimit', 'flowlabel', 'hoplimit', 'tclass')

    # if the local-ip is not set, pick one from the interface !
    # hopefully there is only one, otherwise it will not be very deterministic
    # at time of writing the code currently returns ipv4 before ipv6 in the list

    # XXX: There is no way to trigger an update of the interface source IP if
    # XXX: the underlying interface IP address does change, I believe this
    # XXX: limit/issue is present in vyatta too

    if not options['local'] and options['dhcp-interface']:
        # XXX: This behaviour changes from vyatta which would return 127.0.0.1 if
        # XXX: the interface was not DHCP. As there is no easy way to find if an
        # XXX: interface is using DHCP, and using this feature to get 127.0.0.1
        # XXX: makes little sense, I feel the change in behaviour is acceptable
        picked = get_interface_ip(options['dhcp-interface'])
        if picked == '':
            picked = '127.0.0.1'
            print('Could not get an IP address from {dhcp-interface} using 127.0.0.1 instead')
        options['local'] = picked
        options['dhcp-interface'] = ''

    # get interface addresses (currently effective) - to determine which
    # address is no longer valid and needs to be removed
    # could be done within ConfigurationState
    eff_addr = conf.return_effective_values('address')
    options['addresses-del'] = list_diff(eff_addr, options['addresses-add'])

    # allmulticast fate is linked to multicast
    options['allmulticast'] = options['multicast']

    # check that per encapsulation all local-remote pairs are unique
    conf.set_level('interfaces tunnel')
    ct = conf.get_config_dict()['tunnel']
    options['tunnel'] = {}

    for name in ct:
        tunnel = ct[name]
        encap = tunnel.get('encapsulation', '')
        local = tunnel.get('local-ip', '')
        if not local:
            local = get_interface_ip(tunnel.get('dhcp-interface', ''))
        remote = tunnel.get('remote-ip', '<unset>')
        pair = f'{local}-{remote}'
        options['tunnel'][encap][pair] = options['tunnel'].setdefault(encap, {}).get(pair, 0) + 1

    return conf.to_dict()


def verify(conf):
    options = conf['options']
    changes = conf['changes']
    actions = conf['actions']

    ifname = options['ifname']
    iftype = options['type']

    if changes['section'] == 'delete':
        if ifname in options['nhrp']:
            raise ConfigError(f'Can not delete interface tunnel {iftype} {ifname}, it is used by nhrp')
        # done, bail out early
        return None

    # tunnel encapsulation checks

    if not iftype:
        raise ConfigError(f'Must provide an "encapsulation" for tunnel {iftype} {ifname}')

    if changes['type'] in ('modify', 'delete'):
        # TODO: we could now deal with encapsulation modification by deleting / recreating
        raise ConfigError(f'Encapsulation can only be set at tunnel creation for tunnel {iftype} {ifname}')

    if iftype != 'sit' and options['6rd-prefix']:
        # XXX: should be able to remove this and let the definition catch it
        print(f'6RD can only be configured for sit interfaces not tunnel {iftype} {ifname}')

    # what are the tunnel options we can set / modified / deleted

    kls = get_class(options)
    valid = kls.updates + ['alias', 'addresses-add', 'addresses-del', 'vrf']

    if changes['section'] == 'create':
        valid.extend(['type',])
        valid.extend([o for o in kls.options if o not in kls.updates])

    for create in actions['create']:
        if create not in valid:
            raise ConfigError(f'Can not set "{create}" for tunnel {iftype} {ifname} at tunnel creation')

    for modify in actions['modify']:
        if modify not in valid:
            raise ConfigError(f'Can not modify "{modify}" for tunnel {iftype} {ifname}. it must be set at tunnel creation')

    for delete in actions['delete']:
        if delete in kls.required:
            raise ConfigError(f'Can not remove "{delete}", it is an mandatory option for tunnel {iftype} {ifname}')

    # tunnel information

    tun_local = options['local']
    afi_local = get_afi(tun_local)
    tun_remote = options['remote'] or tun_local
    afi_remote = get_afi(tun_remote)
    tun_ismgre = iftype == 'gre' and not options['remote']
    tun_is6rd = iftype == 'sit' and options['6rd-prefix']

    # incompatible options

    if not tun_local and not options['dhcp-interface'] and not tun_is6rd:
        raise ConfigError(f'Must configure either local-ip or dhcp-interface for tunnel {iftype} {ifname}')

    if tun_local and options['dhcp-interface']:
        raise ConfigError(f'Must configure only one of local-ip or dhcp-interface for tunnel {iftype} {ifname}')

    # tunnel endpoint

    if afi_local != afi_remote:
        raise ConfigError(f'IPv4/IPv6 mismatch between local-ip and remote-ip for tunnel {iftype} {ifname}')

    if afi_local != kls.tunnel:
        version = 4 if tun_local == IP4 else 6
        raise ConfigError(f'Invalid IPv{version} local-ip for tunnel {iftype} {ifname}')

    ipv4_count = len([ip for ip in options['addresses-add'] if is_ipv4(ip)])
    ipv6_count = len([ip for ip in options['addresses-add'] if is_ipv6(ip)])

    if tun_ismgre and afi_local == IP6:
        raise ConfigError(f'Using an IPv6 address is forbidden for mGRE tunnels such as tunnel {iftype} {ifname}')

    # check address family use
    # checks are not enforced (but ip command failing) for backward compatibility

    if ipv4_count and not IP4 in kls.ip:
        print(f'Should not use IPv4 addresses on tunnel {iftype} {ifname}')

    if ipv6_count and not IP6 in kls.ip:
        print(f'Should not use IPv6 addresses on tunnel {iftype} {ifname}')

    # tunnel encapsulation check

    convert = {
        (6, 4, 'gre'):  'ip6gre',
        (6, 6, 'gre'):  'ip6gre',
        (4, 6, 'ipip'): 'ipip6',
        (6, 6, 'ipip'): 'ip6ip6',
    }

    iprotos = []
    if ipv4_count:
        iprotos.append(4)
    if ipv6_count:
        iprotos.append(6)

    for iproto in iprotos:
        replace  = convert.get((kls.tunnel, iproto, iftype), '')
        if replace:
            raise ConfigError(
                f'Using IPv6 address in local-ip or remote-ip is not possible with "encapsulation {iftype}". ' +
                f'Use "encapsulation {replace}" for tunnel {iftype} {ifname} instead.'
            )

    # tunnel options

    incompatible = []
    if afi_local == IP6:
        incompatible.extend(['ttl', 'tos', 'key',])
    if afi_local == IP4:
        incompatible.extend(['encaplimit', 'flowlabel', 'hoplimit', 'tclass'])

    for option in incompatible:
        if option in options:
            # TODO: raise converted to print as not enforced by vyatta
            # raise ConfigError(f'{option} is not valid for tunnel {iftype} {ifname}')
            print(f'Using "{option}" is invalid for tunnel {iftype} {ifname}')

    # duplicate tunnel pairs

    pair = '{}-{}'.format(options['local'], options['remote'])
    if options['tunnel'].get(iftype, {}).get(pair, 0) > 1:
        raise ConfigError(f'More than one tunnel configured for with the same encapulation and IPs for tunnel {iftype} {ifname}')

    return None


def generate(gre):
    return None

def apply(conf):
    options = conf['options']
    changes = conf['changes']
    actions = conf['actions']
    kls = get_class(options)

    # extract ifname as otherwise it is duplicated on the interface creation
    ifname = options.pop('ifname')

    # only the valid keys for creation of a Interface
    config = dict((k, options[k]) for k in kls.options if options[k])

    # setup or create the tunnel interface if it does not exist
    tunnel = kls(ifname, **config)

    if changes['section'] == 'delete':
        tunnel.remove()
        # The perl code was calling/opt/vyatta/sbin/vyatta-tunnel-cleanup
        # which identified tunnels type which were not used anymore to remove them
        # (ie: gre0, gretap0, etc.) The perl code did however nothing
        # This feature is also not implemented yet
        return

    # A GRE interface without remote will be mGRE
    # if the interface does not suppor the option, it skips the change
    for option in tunnel.updates:
        if changes['section'] in 'create' and option in tunnel.options:
            # it was setup at creation
            continue
        tunnel.set_interface(option, options[option])

    # set other interface properties
    for option in ('alias', 'mtu', 'link_detect', 'multicast', 'allmulticast',
                   'vrf', 'ipv6_autoconf', 'ipv6_forwarding', 'ipv6_dad_transmits'):
        tunnel.set_interface(option, options[option])

    # Configure interface address(es)
    for addr in options['addresses-del']:
        tunnel.del_addr(addr)
    for addr in options['addresses-add']:
        tunnel.add_addr(addr)

    # now bring it up (or not)
    tunnel.set_admin_state(options['state'])


if __name__ == '__main__':
    try:
        c = get_config()
        verify(c)
        generate(c)
        apply(c)
    except ConfigError as e:
        print(e)
        exit(1)
