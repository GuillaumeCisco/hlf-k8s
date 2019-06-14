import asyncio

import json
import os
import subprocess
import glob
from shutil import copyfile

from subprocess import call, check_output
from hfc.fabric import Client
from hfc.fabric.orderer import Orderer
from hfc.fabric.organization import create_org
from hfc.fabric.peer import Peer
from hfc.fabric.user import create_user
from hfc.util.keyvaluestore import FileKeyValueStore

cli = Client()

dir_path = os.path.dirname(os.path.realpath(__file__))


def set_env_variables(fabric_cfg_path, msp_dir):

    os.environ['FABRIC_CFG_PATH'] = fabric_cfg_path
    os.environ['CORE_PEER_MSPCONFIGPATH'] = msp_dir
    os.environ['FABRIC_LOGGING_SPEC'] = 'info'


def clean_env_variables():
    del os.environ['FABRIC_CFG_PATH']
    del os.environ['CORE_PEER_MSPCONFIGPATH']
    del os.environ['FABRIC_LOGGING_SPEC']


def set_tls_env_variables(node):

    tls_client_dir = node['tls']['dir']['external'] + '/' + node['tls']['client']['dir']
    tls_server_dir = node['tls']['dir']['external'] + '/' + node['tls']['server']['dir']

    os.environ['CORE_PEER_TLS_ENABLED'] = 'true'
    os.environ['CORE_PEER_TLS_ROOTCERT_FILE'] = tls_server_dir + '/' + node['tls']['server']['ca']
    os.environ['CORE_PEER_TLS_CERT_FILE'] = tls_server_dir + '/' + node['tls']['server']['cert']
    os.environ['CORE_PEER_TLS_KEY_FILE'] = tls_server_dir + '/' + node['tls']['server']['key']

    os.environ['CORE_PEER_TLS_CLIENTAUTHREQUIRED'] = 'true'
    os.environ['CORE_PEER_TLS_CLIENTCERT_FILE'] = tls_client_dir + '/' + node['tls']['client']['cert']
    os.environ['CORE_PEER_TLS_CLIENTKEY_FILE'] = tls_client_dir + '/' + node['tls']['client']['key']
    os.environ['CORE_PEER_TLS_CLIENTROOTCAS_FILES'] = tls_client_dir + '/' + node['tls']['client']['ca']


def clean_tls_env_variables():
    del os.environ['CORE_PEER_TLS_ENABLED']
    del os.environ['CORE_PEER_TLS_ROOTCERT_FILE']
    del os.environ['CORE_PEER_TLS_CERT_FILE']
    del os.environ['CORE_PEER_TLS_KEY_FILE']

    del os.environ['CORE_PEER_TLS_CLIENTAUTHREQUIRED']
    del os.environ['CORE_PEER_TLS_CLIENTCERT_FILE']
    del os.environ['CORE_PEER_TLS_CLIENTKEY_FILE']
    del os.environ['CORE_PEER_TLS_CLIENTROOTCAS_FILES']


def generateChannelArtifacts(conf):
    print(f"Generating channel configuration transaction at {conf['misc']['channel_tx_file']}", flush=True)

    call(['configtxgen',
          '-profile', 'OrgsChannel',
          '-outputCreateChannelTx', conf['misc']['channel_tx_file'],
          '-channelID', conf['misc']['channel_name']])

    print(f"Generating anchor peer update transaction for {conf['name']} at {conf['anchor_tx_file']}", flush=True)

    call(['configtxgen',
          '-profile', 'OrgsChannel',
          '-outputAnchorPeersUpdate', conf['anchor_tx_file'],
          '-channelID', conf['misc']['channel_name'],
          '-asOrg', conf['name']])


# the signer of the channel creation transaction must have admin rights for one of the consortium orgs
# https://stackoverflow.com/questions/45726536/peer-channel-creation-fails-in-hyperledger-fabric
def createChannel(conf, conf_orderer):
    org = conf

    orderer1 = conf_orderer['orderers'][0]

    org_admin = org['users']['admin']

    org_admin_home = org['users']['admin']['home']
    org_admin_msp_dir = os.path.join(org_admin_home, 'msp')

    state_store = FileKeyValueStore('/tmp/kvs/')
    # save org in cli
    cli._organizations.update({org['name']: create_org(org['name'], org, state_store)})

    # register admin in client
    admin_cert_path = os.path.join(org_admin_msp_dir, 'signcerts', 'cert.pem')
    admin_key_path = os.path.join(org_admin_msp_dir, 'keystore', 'key.pem')
    admin = create_user(name=org_admin['name'],
                        org=org['name'],
                        state_store=state_store,
                        msp_id=org['mspid'],
                        cert_path=admin_cert_path,
                        key_path=admin_key_path)
    cli._organizations[org['name']]._users.update({org_admin['name']: admin})

    tls_orderer_client_dir = os.path.join(orderer1['tls']['dir']['external'], orderer1['tls']['client']['dir'])
    orderer = Orderer(orderer1['name'],
                      endpoint=f"{orderer1['host']}:{orderer1['port']['internal']}",
                      tls_ca_cert_file=os.path.join(tls_orderer_client_dir, orderer1['tls']['client']['ca']),
                      client_cert_file=os.path.join(tls_orderer_client_dir, orderer1['tls']['client']['cert']),
                      client_key_file=os.path.join(tls_orderer_client_dir, orderer1['tls']['client']['key']),
                      # opts=(('grpc.ssl_target_name_override', orderer1['host']),)
                      )

    cli._orderers.update({orderer1['name']: orderer})

    loop = asyncio.get_event_loop()
    loop.run_until_complete(cli.channel_create(
        orderer,
        conf['misc']['channel_name'],
        admin,
        config_tx=conf['misc']['channel_tx_file']))


def peersJoinChannel(conf, conf_orderer):
    print(f"Join channel {[x['name'] for x in conf['peers']]} ...", flush=True)

    channel_name = conf['misc']['channel_name']

    state_store = FileKeyValueStore('/tmp/kvs/')

    if conf['name'] not in cli.organizations:
        cli._organizations.update({conf['name']: create_org(conf['name'], conf, state_store)})

    # add channel on cli
    if not cli.get_channel(channel_name):
        cli._channels.update({channel_name: cli.new_channel(channel_name)})

    for peer in conf['peers']:
        tls_peer_client_dir = os.path.join(peer['tls']['dir']['external'], peer['tls']['client']['dir'])

        # add peer in cli
        p = Peer(endpoint=f"{peer['host']}:{peer['port']['internal']}",
                 tls_ca_cert_file=os.path.join(tls_peer_client_dir, peer['tls']['client']['ca']),
                 client_cert_file=os.path.join(tls_peer_client_dir, peer['tls']['client']['cert']),
                 client_key_file=os.path.join(tls_peer_client_dir, peer['tls']['client']['key']))
        cli._peers.update({peer['name']: p})

    org_admin = conf['users']['admin']

    requestor = cli.get_user(conf['name'], org_admin['name'])
    if not requestor:
        org_admin_home = conf['users']['admin']['home']
        org_admin_msp_dir = os.path.join(org_admin_home, 'msp')

        # register admin in client
        admin_cert_path = os.path.join(org_admin_msp_dir, 'signcerts', 'cert.pem')
        admin_key_path = os.path.join(org_admin_msp_dir, 'keystore', 'key.pem')
        requestor = create_user(name=org_admin['name'],
                                org=conf['name'],
                                state_store=state_store,
                                msp_id=conf['mspid'],
                                cert_path=admin_cert_path,
                                key_path=admin_key_path)
        cli._organizations[conf['name']]._users.update({org_admin['name']: requestor})

    # add orderer organization
    if conf_orderer['name'] not in cli.organizations:
        cli._organizations.update({conf_orderer['name']: create_org(conf_orderer['name'], conf_orderer, state_store)})

    # add orderer admin
    orderer_org_admin = conf_orderer['users']['admin']
    orderer_org_admin_home = orderer_org_admin['home']
    orderer_org_admin_msp_dir = os.path.join(orderer_org_admin_home, 'msp')
    orderer_admin_cert_path = os.path.join(orderer_org_admin_msp_dir, 'signcerts', 'cert.pem')
    orderer_admin_key_path = os.path.join(orderer_org_admin_msp_dir, 'keystore', 'key.pem')
    orderer_admin = create_user(name=orderer_org_admin['name'],
                                org=conf_orderer['name'],
                                state_store=state_store,
                                msp_id=conf_orderer['mspid'],
                                cert_path=orderer_admin_cert_path,
                                key_path=orderer_admin_key_path)
    cli._organizations[conf_orderer['name']]._users.update({orderer_org_admin['name']: orderer_admin})

    # add real orderer from orderer organization
    for o in conf_orderer['orderers']:
        orderer = cli.get_orderer(o['name'])
        if not orderer:
            tls_orderer_client_dir = os.path.join(o['tls']['dir']['external'], o['tls']['client']['dir'])
            orderer = Orderer(o['name'],
                              endpoint=f"{o['host']}:{o['port']['internal']}",
                              tls_ca_cert_file=os.path.join(tls_orderer_client_dir, o['tls']['client']['ca']),
                              client_cert_file=os.path.join(tls_orderer_client_dir, o['tls']['client']['cert']),
                              client_key_file=os.path.join(tls_orderer_client_dir, o['tls']['client']['key']),
                              # opts=(('grpc.ssl_target_name_override', o['host']),)
                              )

            cli._orderers.update({o['name']: orderer})

    loop = asyncio.get_event_loop()
    loop.run_until_complete(cli.channel_join(
        requestor=requestor,
        channel_name=conf['misc']['channel_name'],
        peers=[x['name'] for x in conf['peers']],
        orderer=orderer,
        orderer_admin=orderer_admin
    ))


def getChannelConfigBlockWithPeer(org, conf_orderer):
    # :warning: for creating channel make sure env variables CORE_PEER_MSPCONFIGPATH is correctly set

    org_admin_home = org['users']['admin']['home']
    org_admin_msp_dir = org_admin_home + '/msp'

    peer = org['peers'][0]
    peer_core = '/substra/conf/%s/%s' % (org['name'], peer['name'])

    orderer = conf_orderer['orderers'][0]

    # update config path for using right core.yaml and right msp dir
    set_env_variables(peer_core, org_admin_msp_dir)

    tls_peer_client_dir = peer['tls']['dir']['external'] + '/' + peer['tls']['client']['dir']
    tls_orderer_client_dir = orderer['tls']['dir']['external'] + '/' + orderer['tls']['client']['dir']

    call([
        'peer',
        'channel',
        'fetch',
        'config',
        org['misc']['channel_block'],
        '-c', org['misc']['channel_name'],
        '-o', '%(host)s:%(port)s' % {'host': orderer['host'], 'port': orderer['port']['internal']},
        '--tls',
        '--cafile', tls_orderer_client_dir + '/' + orderer['tls']['client']['ca'],
        '--clientauth',
        '--certfile', tls_peer_client_dir + '/' + peer['tls']['client']['cert'],
        '--keyfile', tls_peer_client_dir + '/' + peer['tls']['client']['key']
    ])

    # clean env variables
    clean_env_variables()


def createChannelConfig(org, with_anchor=True):
    org_config = check_output(['configtxgen', '-printOrg', org['name']])

    org_config = json.loads(org_config.decode('utf-8'))

    if with_anchor:
        # Add Anchor peer
        peer = org['peers'][0]
        org_config['values']['AnchorPeers'] = {'mod_policy': 'Admins',
                                               'value': {'anchor_peers': [{'host': peer['host'],
                                                                           'port': peer['port']['internal']}]},
                                               'version': '0'}

    return org_config


def createUpdateProposal(conf, org__channel_config, input_block, channel_name):
    call(['configtxlator',
          'proto_decode',
          '--input', input_block,
          '--type', 'common.Block',
          '--output', 'mychannelconfig.json'])

    my_channel_config = json.load(open('mychannelconfig.json', 'r'))

    # Keep useful part
    my_channel_config = my_channel_config['data']['data'][0]['payload']['data']['config']
    json.dump(my_channel_config, open('mychannelconfig.json', 'w'))

    # Add org
    my_channel_config['channel_group']['groups']['Application']['groups'][conf['name']] = org__channel_config
    json.dump(my_channel_config, open('mychannelconfigupdate.json', 'w'))

    # Compute diff
    call(['configtxlator',
          'proto_encode',
          '--input', 'mychannelconfig.json',
          '--type', 'common.Config',
          '--output', 'mychannelconfig.pb'])

    call(['configtxlator',
          'proto_encode',
          '--input', 'mychannelconfigupdate.json',
          '--type', 'common.Config',
          '--output', 'mychannelconfigupdate.pb'])

    call(['configtxlator',
          'compute_update',
          '--channel_id', channel_name,
          '--original', 'mychannelconfig.pb',
          '--updated', 'mychannelconfigupdate.pb',
          '--output', 'compute_update.pb'])

    call(['configtxlator',
          'proto_decode',
          '--input', 'compute_update.pb',
          '--type', 'common.ConfigUpdate',
          '--output', 'compute_update.json'])

    # Prepare proposal
    update = json.load(open('compute_update.json', 'r'))
    proposal = {'payload': {'header': {'channel_header': {'channel_id': channel_name,
                                                          'type': 2}},
                            'data': {'config_update': update}}}

    json.dump(proposal, open('proposal.json', 'w'))
    call(['configtxlator',
          'proto_encode',
          '--input', 'proposal.json',
          '--type', 'common.Envelope',
          '--output', 'proposal.pb'])


def signAndPushUpdateProposal(orgs, conf_orderer, channel_name):
    orderer = conf_orderer['orderers'][0]

    for org in orgs:
        # Sign
        org_admin_home = org['users']['admin']['home']
        org_admin_msp_dir = org_admin_home + '/msp'

        peer = org['peers'][0]
        peer_core = '/substra/conf/%s/%s' % (org['name'], peer['name'])

        set_env_variables(peer_core, org_admin_msp_dir)

        tls_peer_client_dir = peer['tls']['dir']['external'] + '/' + peer['tls']['client']['dir']
        tls_orderer_client_dir = orderer['tls']['dir']['external'] + '/' + orderer['tls']['client']['dir']

        print('Sign update proposal on %(PEER_HOST)s ...' % {'PEER_HOST': peer['host']}, flush=True)

        # One signature per proposal
        proposal_file = 'proposal-%s-%s.pb' % (org['name'], peer['name'])
        copyfile('proposal.pb', proposal_file)

        call(['peer',
              'channel', 'signconfigtx',
              '-f', proposal_file,
              '-o', '%(host)s:%(port)s' % {'host': orderer['host'], 'port': orderer['port']['internal']},
              '--tls',
              '--cafile', tls_orderer_client_dir + '/' + orderer['tls']['client']['ca'],
              # https://hyperledger-fabric.readthedocs.io/en/release-1.1/enable_tls.html#configuring-tls-for-the-peer-cli
              '--clientauth',
              '--certfile', tls_peer_client_dir + '/' + peer['tls']['client']['cert'],
              '--keyfile', tls_peer_client_dir + '/' + peer['tls']['client']['key']
              ])

        call(['configtxlator',
              'proto_decode',
              '--input', proposal_file,
              '--type', 'common.Envelope',
              '--output', 'proposal-%s-%s.json' % (org['name'], peer['name'])])

        # clean env variables
        clean_env_variables()
    else:
        # List all signed proposal
        files = glob.glob('./proposal-*.json')
        files.sort(key=os.path.getmtime)
        proposals = [json.load(open(file_path, 'r')) for file_path in files]

        # Take the first signed proposal
        proposal = proposals.pop()

        # Merge signatures into first signed proposal
        for p in proposals:
            proposal['payload']['data']['signatures'].extend(p['payload']['data']['signatures'])
        json.dump(proposal, open('proposal-signed.json', 'w'))

        # Convert it to protobuf
        call(['configtxlator',
              'proto_encode',
              '--input', 'proposal-signed.json',
              '--type', 'common.Envelope',
              '--output', 'proposal-signed.pb'])

        # Push
        org_admin_home = org['users']['admin']['home']
        org_admin_msp_dir = org_admin_home + '/msp'

        peer = org['peers'][0]
        peer_core = '/substra/conf/%s/%s' % (org['name'], peer['name'])

        set_env_variables(peer_core, org_admin_msp_dir)

        tls_peer_client_dir = peer['tls']['dir']['external'] + '/' + peer['tls']['client']['dir']
        tls_orderer_client_dir = orderer['tls']['dir']['external'] + '/' + orderer['tls']['client']['dir']

        print('Send update proposal on %(PEER_HOST)s ...' % {'PEER_HOST': peer['host']}, flush=True)

        call(['peer',
              'channel', 'update',
              '-f', 'proposal-signed.pb',
              '-c', channel_name,
              '-o', '%(host)s:%(port)s' % {'host': orderer['host'], 'port': orderer['port']['internal']},
              '--tls',
              '--cafile', tls_orderer_client_dir + '/' + orderer['tls']['client']['ca'],
              # https://hyperledger-fabric.readthedocs.io/en/release-1.1/enable_tls.html#configuring-tls-for-the-peer-cli
              '--clientauth',
              '--certfile', tls_peer_client_dir + '/' + peer['tls']['client']['cert'],
              '--keyfile', tls_peer_client_dir + '/' + peer['tls']['client']['key']
              ])

        # clean env variables
        clean_env_variables()


def generateChannelUpdate(conf, conf_externals, orderer):
    org_channel_config = createChannelConfig(conf)
    getChannelConfigBlockWithOrderer(orderer, conf['misc']['channel_name'], 'mychannelconfig.block')

    createUpdateProposal(conf, org_channel_config, 'mychannelconfig.block', conf['misc']['channel_name'])
    external_orgs = [conf_org for conf_org in conf_externals]
    signAndPushUpdateProposal(external_orgs, orderer, conf['misc']['channel_name'])


# # the updater of the channel anchor transaction must have admin rights for one of the consortium orgs
# Update the anchor peers
def updateAnchorPeers(conf, conf_orderer):
    # :warning: for updating anchor peers make sure env variables CORE_PEER_MSPCONFIGPATH is correctly set

    org = conf
    org_admin_home = org['users']['admin']['home']
    org_admin_msp_dir = os.path.join(org_admin_home, 'msp')

    peer = org['peers'][0]
    peer_core = '/substra/conf/%s/%s' % (org['name'], peer['name'])

    orderer = conf_orderer['orderers'][0]

    print('Updating anchor peers for %(peer_host)s ...' % {'peer_host': org['peers'][0]['host']}, flush=True)

    # update config path for using right core.yaml and right msp dir
    set_env_variables(peer_core, org_admin_msp_dir)

    tls_peer_client_dir = peer['tls']['dir']['external'] + '/' + peer['tls']['client']['dir']
    tls_orderer_client_dir = orderer['tls']['dir']['external'] + '/' + orderer['tls']['client']['dir']

    call(['peer',
          'channel', 'update',
          '-c', conf['misc']['channel_name'],
          '-f', org['anchor_tx_file'],
          '-o', '%(host)s:%(port)s' % {'host': orderer['host'], 'port': orderer['port']['internal']},
          '--tls',
          '--cafile', tls_orderer_client_dir + '/' + orderer['tls']['client']['ca'],
          # https://hyperledger-fabric.readthedocs.io/en/release-1.1/enable_tls.html#configuring-tls-for-the-peer-cli
          '--clientauth',
          '--certfile', tls_peer_client_dir + '/' + peer['tls']['client']['cert'],
          '--keyfile', tls_peer_client_dir + '/' + peer['tls']['client']['key']
          ])

    # clean env variables
    clean_env_variables()


def installChainCodeOnPeers(org, chaincode_version):
    print(f"Installing chaincode on {[x['name'] for x in org['peers']]} ...", flush=True)

    chaincode_name = org['misc']['chaincode_name']
    chaincode_path = org['misc']['chaincode_path']
    channel_name = org['misc']['channel_name']

    state_store = FileKeyValueStore('/tmp/kvs/')

    if org['name'] not in cli.organizations:
        cli._organizations.update({org['name']: create_org(org['name'], org, state_store)})

    # add channel on cli
    if not cli.get_channel(channel_name):
        cli._channels.update({channel_name: cli.new_channel(channel_name)})

    for peer in org['peers']:
        if not cli.get_peer(peer['name']):
            tls_peer_client_dir = os.path.join(peer['tls']['dir']['external'], peer['tls']['client']['dir'])

            # add peer in cli
            p = Peer(endpoint=f"{peer['host']}:{peer['port']['internal']}",
                     tls_ca_cert_file=os.path.join(tls_peer_client_dir, peer['tls']['client']['ca']),
                     client_cert_file=os.path.join(tls_peer_client_dir, peer['tls']['client']['cert']),
                     client_key_file=os.path.join(tls_peer_client_dir, peer['tls']['client']['key']))
            cli._peers.update({peer['name']: p})

    org_admin = org['users']['admin']
    org_admin_home = org_admin['home']
    org_admin_msp_dir = os.path.join(org_admin_home, 'msp')

    requestor = cli.get_user(org['name'], org_admin['name'])
    if not requestor:
        # register admin in client
        admin_cert_path = os.path.join(org_admin_msp_dir, 'signcerts', 'cert.pem')
        admin_key_path = os.path.join(org_admin_msp_dir, 'keystore', 'key.pem')
        requestor = create_user(name=org_admin['name'],
                                org=org['name'],
                                state_store=state_store,
                                msp_id=org['mspid'],
                                cert_path=admin_cert_path,
                                key_path=admin_key_path)
        cli._organizations[org['name']]._users.update({org_admin['name']: requestor})

    loop = asyncio.get_event_loop()
    loop.run_until_complete(cli.chaincode_install(
        requestor=requestor,
        peers=[x['name'] for x in org['peers']],
        cc_path=chaincode_path,
        cc_name=chaincode_name,
        cc_version=chaincode_version
    ))


def getChaincodeVersion(conf, conf_orderer):
    org = conf

    peer = org['peers'][0]
    peer_core = '/substra/conf/%s/%s' % (org['name'], peer['name'])

    org_admin_home = org['users']['admin']['home']
    org_admin_msp_dir = org_admin_home + '/msp'

    orderer = conf_orderer['orderers'][0]

    # update config path for using right core.yaml and right msp dir
    set_env_variables(peer_core, org_admin_msp_dir)
    set_tls_env_variables(peer)

    tls_peer_client_dir = peer['tls']['dir']['external'] + '/' + peer['tls']['client']['dir']
    tls_orderer_client_dir = orderer['tls']['dir']['external'] + '/' + orderer['tls']['client']['dir']

    output = subprocess.run(['peer',
                             'chaincode', 'list',
                             '-C', conf['misc']['channel_name'],
                             '--instantiated',
                             '-o', '%(host)s:%(port)s' % {'host': orderer['host'], 'port': orderer['port']['internal']},
                             '--tls',
                             '--cafile', tls_orderer_client_dir + '/' + orderer['tls']['client']['ca'],
                             # https://hyperledger-fabric.readthedocs.io/en/release-1.1/enable_tls.html#configuring-tls-for-the-peer-cli
                             '--clientauth',
                             '--certfile', tls_peer_client_dir + '/' + peer['tls']['client']['cert'],
                             '--keyfile', tls_peer_client_dir + '/' + peer['tls']['client']['key']
                             ],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    data = output.stdout.decode('utf-8')

    clean_tls_env_variables()
    clean_env_variables()

    return float(data.split('Version: ')[-1].split(',')[0])


def makePolicy(orgs_mspid):
    policy = 'OR('

    for index, org_mspid in enumerate(orgs_mspid):
        if index != 0:
            policy += ','
        policy += '\'' + org_mspid + '.member\''

    policy += ')'
    print('policy: %s' % policy, flush=True)

    return policy


def instanciateChaincode(conf, args=None):

    policy = makePolicy([conf['mspid']])

    org_admin = conf['users']['admin']

    channel_name = conf['misc']['channel_name']
    chaincode_name = conf['misc']['chaincode_name']
    chaincode_version = conf['misc']['chaincode_version']

    requestor = cli.get_user(conf['name'], org_admin['name'])
    loop = asyncio.get_event_loop()

    loop.run_until_complete(cli.chaincode_instantiate(
        requestor=requestor,
        channel_name=channel_name,
        peers=[x['name'] for x in conf['peers']],
        args=args,
        cc_name=chaincode_name,
        cc_version=chaincode_version,
        cc_endorsement_policy=policy,
        wait_for_event=True
    ))


def upgradeChainCode(conf, args, conf_orderer, orgs_mspid, chaincode_version):
    policy = makePolicy(orgs_mspid)

    org = conf

    peer = org['peers'][0]
    peer_core = '/substra/conf/%s/%s' % (org['name'], peer['name'])

    org_admin_home = org['users']['admin']['home']
    org_admin_msp_dir = org_admin_home + '/msp'

    orderer = conf_orderer['orderers'][0]

    # update config path for using right core.yaml and right msp dir
    set_env_variables(peer_core, org_admin_msp_dir)
    set_tls_env_variables(peer)

    print('Upgrading chaincode on %(PEER_HOST)s ...' % {'PEER_HOST': peer['host']}, flush=True)

    tls_peer_client_dir = peer['tls']['dir']['external'] + '/' + peer['tls']['client']['dir']
    tls_orderer_client_dir = orderer['tls']['dir']['external'] + '/' + orderer['tls']['client']['dir']

    call(['peer',
          'chaincode', 'upgrade',
          '-C', conf['misc']['channel_name'],
          '-n', conf['misc']['chaincode_name'],
          '-v', chaincode_version,
          '-c', args,
          '-P', policy,
          '-o', '%(host)s:%(port)s' % {'host': orderer['host'], 'port': orderer['port']['internal']},
          '--tls',
          '--cafile', tls_orderer_client_dir + '/' + orderer['tls']['client']['ca'],
          # https://hyperledger-fabric.readthedocs.io/en/release-1.1/enable_tls.html#configuring-tls-for-the-peer-cli
          '--clientauth',
          '--certfile', tls_peer_client_dir + '/' + peer['tls']['client']['cert'],
          '--keyfile', tls_peer_client_dir + '/' + peer['tls']['client']['key']
          ])

    # clean env variables
    clean_tls_env_variables()
    clean_env_variables()


def queryChaincodeFromFirstPeerFirstOrg(conf, chaincode_version=None):
    org = conf
    org_admin = conf['users']['admin']
    peer = org['peers'][0]

    print('Try to query chaincode from first peer first org before invoke', flush=True)

    channel_name = conf['misc']['channel_name']
    chaincode_name = conf['misc']['chaincode_name']

    requestor = cli.get_user(conf['name'], org_admin['name'])

    loop = asyncio.get_event_loop()
    response = loop.run_until_complete(cli.chaincode_query(
        requestor=requestor,
        channel_name=channel_name,
        peers=[peer['name']],
        fcn='queryObjectives',
        args=None,
        cc_name=chaincode_name,
    ))

    return response


def createSystemUpdateProposal(org, conf_orderer):
    # https://console.bluemix.net/docs/services/blockchain/howto/orderer_operate.html?locale=en#orderer-operate

    channel_name = org['misc']['system_channel_name']
    channel_block = org['misc']['system_channel_block']
    org_config = createChannelConfig(org, False)
    system_channel_config_envelope = getSystemChannelConfigBlock(conf_orderer, channel_block)
    system_channel_config = system_channel_config_envelope['config']

    # call(['configtxlator',
    #       'proto_decode',
    #       '--input', channel_block,
    #       '--type', 'common.Block',
    #       '--output', 'system_channelconfig.json'])
    #system_channel_config = json.load(open('system_channelconfig.json', 'r'))

    # Keep useful part
    #system_channel_config = system_channel_config['data']['data'][0]['payload']['data']['config']

    json.dump(system_channel_config, open('system_channelconfig.json', 'w'))
    call(['configtxlator',
          'proto_encode',
          '--input', 'system_channelconfig.json',
          '--type', 'common.Config',
          '--output', 'systemchannelold.block'])

    # Update useful part
    system_channel_config['channel_group']['groups']['Consortiums']['groups']['SampleConsortium']['groups'][
        org['name']] = org_config
    json.dump(system_channel_config, open('system_channelconfig.json', 'w'))
    call(['configtxlator',
          'proto_encode',
          '--input', 'system_channelconfig.json',
          '--type', 'common.Config',
          '--output', 'systemchannelupdate.block'])

    # Compute update
    call(' '.join(['configtxlator',
                   'compute_update',
                   '--channel_id', channel_name,
                   '--original', 'systemchannelold.block',
                   '--updated', 'systemchannelupdate.block',
                   ' | ', 'configtxlator',
                   'proto_decode',
                   '--type', 'common.ConfigUpdate',
                   '--output', 'compute_update.json']),
         shell=True)

    # Prepare proposal
    update = json.load(open('compute_update.json', 'r'))

    proposal = {'payload': {'header': {'channel_header': {'channel_id': channel_name,
                                                          'type': 2}},
                            'data': {'config_update': update}}}

    json.dump(proposal, open('proposal.json', 'w'))

    config_tx_file = 'proposal.pb'

    call(['configtxlator',
          'proto_encode',
          '--input', 'proposal.json',
          '--type', 'common.Envelope',
          '--output', config_tx_file])

    return config_tx_file


def getSystemChannelConfigBlock(conf_orderer, block_name):
    reutrn getChannelConfigBlockWithOrderer(conf_orderer, conf_orderer['misc']['system_channel_name'], block_name)


def getChannelConfigBlockWithOrderer(conf, channel_name, block_name):
    print('Will getChannelConfigBlockWithOrderer', flush=True)

    state_store = FileKeyValueStore('/tmp/kvs/')

    # add channel on cli
    if not cli.get_channel(channel_name):
        cli._channels.update({channel_name: cli.new_channel(channel_name)})
    # create peer and make orderer a peer too for fetching config
    for peer in conf['peers']:
        if not cli.get_peer(peer['name']):
            tls_peer_client_dir = os.path.join(peer['tls']['dir']['external'], peer['tls']['client']['dir'])
            # add peer in cli
            p = Peer(endpoint=f"{peer['host']}:{peer['port']['internal']}",
                     tls_ca_cert_file=os.path.join(tls_peer_client_dir, peer['tls']['client']['ca']),
                     client_cert_file=os.path.join(tls_peer_client_dir, peer['tls']['client']['cert']),
                     client_key_file=os.path.join(tls_peer_client_dir, peer['tls']['client']['key']))
            cli._peers.update({peer['name']: p})

    # add orderer organization
    if conf['name'] not in cli.organizations:
        cli._organizations.update({conf['name']: create_org(conf['name'], conf, state_store)})

    # # add orderer admin
    orderer_org_admin = conf['users']['admin']
    orderer_org_admin_home = orderer_org_admin['home']
    orderer_org_admin_msp_dir = os.path.join(orderer_org_admin_home, 'msp')
    orderer_admin_cert_path = os.path.join(orderer_org_admin_msp_dir, 'signcerts', 'cert.pem')
    orderer_admin_key_path = os.path.join(orderer_org_admin_msp_dir, 'keystore', 'key.pem')
    orderer_admin = create_user(name=orderer_org_admin['name'],
                                org=conf['name'],
                                state_store=state_store,
                                msp_id=conf['mspid'],
                                cert_path=orderer_admin_cert_path,
                                key_path=orderer_admin_key_path)
    cli._organizations[conf['name']]._users.update({orderer_org_admin['name']: orderer_admin})

    # add real orderer from orderer organization
    for o in conf['orderers']:
        orderer = cli.get_orderer(o['name'])
        if not orderer:
            tls_orderer_client_dir = os.path.join(o['tls']['dir']['external'], o['tls']['client']['dir'])
            orderer = Orderer(o['name'],
                              endpoint=f"{o['host']}:{o['port']['internal']}",
                              tls_ca_cert_file=os.path.join(tls_orderer_client_dir, o['tls']['client']['ca']),
                              client_cert_file=os.path.join(tls_orderer_client_dir, o['tls']['client']['cert']),
                              client_key_file=os.path.join(tls_orderer_client_dir, o['tls']['client']['key']),
                              )

            cli._orderers.update({o['name']: orderer})


    orderer = conf['orderers'][0]
    #orderer_core = os.path.join('/substra/conf', conf['name'], orderer['name'])

    peer = conf['peers'][0]
    peer_core = os.path.join('/substra/conf', conf['name'], peer['name'])

    loop = asyncio.get_event_loop()
    config_envelope = loop.run_until_complete(cli.get_channel_config_with_orderer(
        orderer=cli.get_orderer(orderer['name']),
        requestor=orderer_admin,
        channel_name=channel_name
    ))

    print('got ChannelConfigBlockWithOrderer', flush=True)

    return config_envelope

    # set_env_variables(peer_core, orderer_org_admin_msp_dir)
    #
    # #tls_orderer_client_dir = os.path.join(orderer['tls']['dir']['external'], orderer['tls']['client']['dir'])
    # tls_peer_client_dir = os.path.join(peer['tls']['dir']['external'], peer['tls']['client']['dir'])
    #
    # call([
    #     'peer',
    #     'channel',
    #     'fetch',
    #     'config',
    #     block_name,
    #     '-c', channel_name,
    #     '-o', f"{orderer['host']}:{orderer['port']['internal']}",
    #     '--tls',
    #     '--clientauth',
    #     '--cafile', os.path.join(tls_peer_client_dir, peer['tls']['client']['ca']),
    #     '--certfile', os.path.join(tls_peer_client_dir, peer['tls']['client']['cert']),
    #     '--keyfile', os.path.join(tls_peer_client_dir, peer['tls']['client']['key'])
    # ])
    #
    # call(['cat', block_name])
    #
    # # clean env variables
    # clean_env_variables()


def signAndPushSystemUpdateProposal(org, config_tx_file):
    print('signAndPushSystemUpdateProposal')

    channel_name = org['misc']['system_channel_name']

    state_store = FileKeyValueStore('/tmp/kvs/')

    # add channel on cli
    if not cli.get_channel(channel_name):
        cli._channels.update({channel_name: cli.new_channel(channel_name)})

    # add orderer organization
    if org['name'] not in cli.organizations:
        cli._organizations.update({org['name']: create_org(org['name'], org, state_store)})

    # add orderer admin
    orderer_org_admin = org['users']['admin']
    orderer_org_admin_home = orderer_org_admin['home']
    orderer_org_admin_msp_dir = os.path.join(orderer_org_admin_home, 'msp')
    orderer_admin_cert_path = os.path.join(orderer_org_admin_msp_dir, 'signcerts', 'cert.pem')
    orderer_admin_key_path = os.path.join(orderer_org_admin_msp_dir, 'keystore', 'key.pem')
    orderer_admin = create_user(name=orderer_org_admin['name'],
                                org=org['name'],
                                state_store=state_store,
                                msp_id=org['mspid'],
                                cert_path=orderer_admin_cert_path,
                                key_path=orderer_admin_key_path)
    cli._organizations[org['name']]._users.update({orderer_org_admin['name']: orderer_admin})

    # add real orderer from orderer organization
    for o in org['orderers']:
        orderer = cli.get_orderer(o['name'])
        if not orderer:
            tls_orderer_client_dir = os.path.join(o['tls']['dir']['external'], o['tls']['client']['dir'])
            orderer = Orderer(o['name'],
                              endpoint=f"{o['host']}:{o['port']['internal']}",
                              tls_ca_cert_file=os.path.join(tls_orderer_client_dir, o['tls']['client']['ca']),
                              client_cert_file=os.path.join(tls_orderer_client_dir, o['tls']['client']['cert']),
                              client_key_file=os.path.join(tls_orderer_client_dir, o['tls']['client']['key']),
                              # opts=(('grpc.ssl_target_name_override', o['host']),)
                              )

            cli._orderers.update({o['name']: orderer})

    loop = asyncio.get_event_loop()
    loop.run_until_complete(cli.channel_update(
        orderer,
        channel_name,
        orderer_admin,
        config_tx=config_tx_file))
