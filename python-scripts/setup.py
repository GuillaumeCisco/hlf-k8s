import json
from subprocess import call

from utils.setup_utils import registerIdentities, registerUsers, generateGenesis, enrollWithFiles
from utils.common_utils import genTLSCert, create_directory
from shutil import copyfile


def generateMSPandTLS(node, org):

    # Node peer/orderer mounted volume, see docker_utils 'Client/Server TLS' binded volume.
    tls_setup_dir = node['tls']['dir']['external']

    # create external folders (client and server)
    tls_server_dir = tls_setup_dir + '/' + node['tls']['server']['dir']
    tls_client_dir = tls_setup_dir + '/' + node['tls']['client']['dir']
    create_directory(tls_server_dir)
    create_directory(tls_client_dir)

    # Generate server TLS cert and key pair in container
    genTLSCert(node, node['host'], org,
               cert_file='%s/%s' % (tls_server_dir, node['tls']['server']['cert']),
               key_file='%s/%s' % (tls_server_dir, node['tls']['server']['key']),
               ca_file='%s/%s' % (tls_server_dir, node['tls']['server']['ca']))

    # Generate client TLS cert and key pair for the peer CLI (will be used by external tools)
    # in a binded volume
    genTLSCert(node, node['name'], org,
               cert_file='%s/%s' % (tls_client_dir, node['tls']['client']['cert']),
               key_file='%s/%s' % (tls_client_dir, node['tls']['client']['key']),
               ca_file='%s/%s' % (tls_client_dir, node['tls']['client']['ca']))

    # Enroll the node to get an enrollment certificate and set up the core's local MSP directory for starting node
    setup_node_msp_dir = org['core_dir']['internal'] + '/' + node['name'] + '/msp'
    enrollWithFiles(node, org, setup_node_msp_dir)

    return setup_node_msp_dir


def init_org(conf):

    for peer in conf['peers']:

        # remove ugly sample files defined here https://github.com/hyperledger/fabric/tree/master/sampleconfig
        # except core.yaml from binded volume
        # call('cd $FABRIC_CA_CLIENT_HOME && rm -rf msp orderer.yaml configtx.yaml', shell=True)

        ##################################################################################################################
        # Although a peer may use the same TLS key and certificate file for both inbound and outbound TLS,               #
        # we generate a different key and certificate for inbound and outbound TLS simply to show that it is permissible #
        ##################################################################################################################

        setup_peer_msp_dir = generateMSPandTLS(peer, conf)

        # copy the admincerts from the admin user for being able to install chaincode
        # https://stackoverflow.com/questions/48221810/what-is-difference-between-admincerts-and-signcerts-in-hyperledge-fabric-msp
        # https://lists.hyperledger.org/g/fabric/topic/17549225#1250
        # https://github.com/hyperledger/fabric-sdk-go/blob/master/internal/github.com/hyperledger/fabric/msp/mspimpl.go#L460
        # https://jira.hyperledger.org/browse/FAB-3840
        admin = conf['users']['admin']
        org_admin_msp_dir = admin['home'] + '/msp'
        dst_admincerts_dir = setup_peer_msp_dir + '/admincerts'
        create_directory(dst_admincerts_dir)
        copyfile(org_admin_msp_dir + '/signcerts/cert.pem', '%s/%s-cert.pem' % (dst_admincerts_dir, admin['name']))


def init_orderer(conf):
    # remove ugly sample files defined here https://github.com/hyperledger/fabric/tree/master/sampleconfig
    # except orderer.yaml from binded volume
    # call('cd $FABRIC_CA_CLIENT_HOME && rm -rf msp core.yaml configtx.yaml', shell=True)

    for orderer in conf['orderers']:
        setup_orderer_msp_dir = generateMSPandTLS(orderer, conf)

        # copy the admincerts from the admin user for being able to launch orderer
        # https://stackoverflow.com/questions/48221810/what-is-difference-between-admincerts-and-signcerts-in-hyperledge-fabric-msp
        # https://lists.hyperledger.org/g/fabric/topic/17549225#1250
        dst_admincerts_dir = setup_orderer_msp_dir + '/admincerts'
        create_directory(dst_admincerts_dir)
        copyfile('%s/signcerts/cert.pem' % setup_orderer_msp_dir, '%s/%s-cert.pem' % (dst_admincerts_dir, orderer['name']))


def init(conf):
    if 'peers' in conf:
        init_org(conf)
    if 'orderers' in conf:
        init_orderer(conf)
        create_directory(conf['broadcast_dir']['external'])
        generateGenesis(conf)


if __name__ == '__main__':

    conf = json.load(open('/substra/conf.json', 'r'))
    registerIdentities(conf)
    registerUsers(conf)
    init(conf)
    print('Finished setup', flush=True)
    call(['touch', conf['misc']['setup_success_file']])
