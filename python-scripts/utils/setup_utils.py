import os
from shutil import copytree
from subprocess import call

from hfc.fabric_ca.caservice import ca_service
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes


from .common_utils import waitPort, dowait


def removeIntermediateCerts(intermediatecerts_dir):
    print('Delete intermediate certs in ' + intermediatecerts_dir, flush=True)
    if os.path.exists(intermediatecerts_dir):
        for file in os.listdir(intermediatecerts_dir):
            file_path = os.path.join(intermediatecerts_dir, file)
            if os.path.isfile(file_path):
                os.remove(file_path)


def completeMSPSetup(org_msp_dir):
    src = org_msp_dir + '/cacerts/'
    dst = org_msp_dir + '/tlscacerts'

    if not os.path.exists(dst):
        copytree(src, dst)

    # intermediate cacert management
    if os.path.exists(org_msp_dir + '/intermediatecerts'):
        # no intermediate cert in this config, delete generated files for not seeing warning
        removeIntermediateCerts(org_msp_dir + '/intermediatecerts/')

        # uncomment if using intermediate certs
        # copytree(org_msp_dir + '/intermediatecerts/', org_msp_dir + '/tlsintermediatecerts/')


def configLocalMSP(org, user_name):
    user = org['users'][user_name]
    org_user_home = user['home']
    org_user_msp_dir = org_user_home + '/msp'

    # if local admin msp does not exist, create it by enrolling user
    if not os.path.exists(org_user_msp_dir):
        print('Enroll user and copy in admincert for configtxgen', flush=True)

        # wait ca certfile exists before enrolling
        dowait('%(ca_name)s to start' % {'ca_name': org['ca']['name']},
               90,
               org['ca']['logfile'],
               [org['ca']['certfile']['internal']])

        msg = 'Enrolling user \'%(user_name)s\' for organization %(org)s with %(ca_host)s and home directory %(org_user_home)s...'
        print(msg % {
            'user_name': user['name'],
            'org': org['name'],
            'ca_host': org['ca']['host'],
            'org_user_home': org_user_home
        }, flush=True)

        # admincerts is required for configtxgen binary
        return enrollWithFiles(user, org, org_user_msp_dir, admincerts=True)


def enrollCABootstrapAdmin(org):

    waitPort('%(CA_NAME)s to start' % {'CA_NAME': org['ca']['name']},
             90,
             org['ca']['logfile'],
             org['ca']['host'],
             org['ca']['port']['internal'])
    print('Enrolling with %(CA_NAME)s as bootstrap identity ...' % {'CA_NAME': org['ca']['name']}, flush=True)

    # create ca-cert.pem file
    target = "https://%s:%s" % (org['ca']['host'], org['ca']['port']['internal'])
    cacli = ca_service(target=target,
                       ca_certs_path=org['ca']['certfile']['internal'],
                       ca_name=org['ca']['name'])
    bootstrap_admin = cacli.enroll(org['users']['bootstrap_admin']['name'], org['users']['bootstrap_admin']['pass'])
    return bootstrap_admin


def registerOrdererIdentities(org):
    badmin = enrollCABootstrapAdmin(org)

    for orderer in org['orderers']:
        print('Registering %(orderer_name)s with %(ca_name)s' % {'orderer_name': orderer['name'],
                                                                 'ca_name': org['ca']['name']},
              flush=True)

        badmin.register(orderer['name'], orderer['pass'], 'orderer', maxEnrollments=-1)

    print('Registering admin identity with %(ca_name)s' % {'ca_name': org['ca']['name']}, flush=True)
    badmin.register(org['users']['admin']['name'], org['users']['admin']['pass'], maxEnrollments=-1, attrs=[{'admin': 'true:ecert'}])


def registerPeerIdentities(org):
    badmin = enrollCABootstrapAdmin(org)
    for peer in org['peers']:
        print('Registering %(peer_name)s with %(ca_name)s\n' % {'peer_name': peer['name'],
                                                                'ca_name': org['ca']['name']}, flush=True)
        badmin.register(peer['name'], peer['pass'], 'peer', maxEnrollments=-1)

    print('Registering admin identity with %(ca_name)s' % {'ca_name': org['ca']['name']}, flush=True)
    # The admin identity has the "admin" attribute which is added to ECert by default
    attrs = [
        {'hf.Registrar.Roles': 'client'},
        {'hf.Registrar.Attributess': '*'},
        {'hf.Revoker': 'true'},
        {'hf.GenCRL': 'true'},
        {'admin': 'true:ecert'},
        {'abac.init': 'true:ecert'}
    ]
    badmin.register(org['users']['admin']['name'], org['users']['admin']['pass'], maxEnrollments=-1, attrs=attrs)

    print('Registering user identity with %(ca_name)s\n' % {'ca_name': org['ca']['name']}, flush=True)
    badmin.register(org['users']['user']['name'], org['users']['user']['pass'], maxEnrollments=-1)


def registerIdentities(conf):
    if 'peers' in conf:
        registerPeerIdentities(conf)
    if 'orderers' in conf:
        registerOrdererIdentities(conf)


def registerUsers(conf):
    print('Getting CA certificates ...\n', flush=True)

    if 'peers' in conf:
        org_admin_msp_dir = conf['users']['admin']['home'] + '/msp'

        # will create admin and user folder with an msp folder and populate it. Populate admincerts for configtxgen to work
        # https://hyperledger-fabric.readthedocs.io/en/release-1.2/msp.html?highlight=admincerts#msp-setup-on-the-peer-orderer-side
        # https://stackoverflow.com/questions/48221810/what-is-difference-between-admincerts-and-signcerts-in-hyperledge-fabric-msp

        # enroll admin and create admincerts
        enrollmentAdmin = configLocalMSP(conf, 'admin')
        # needed for tls communication for create channel from peer for example, copy tlscacerts from cacerts
        completeMSPSetup(org_admin_msp_dir)

        # enroll user and create admincerts
        configLocalMSP(conf, 'user')
    else:
        org_admin_msp_dir = conf['users']['admin']['home'] + '/msp'

        # https://hyperledger-fabric.readthedocs.io/en/release-1.2/msp.html?highlight=admincerts#msp-setup-on-the-peer-orderer-side
        # https://stackoverflow.com/questions/48221810/what-is-difference-between-admincerts-and-signcerts-in-hyperledge-fabric-msp
        # will create admincerts for configtxgen to work

        # enroll admin and create admincerts
        enrollmentAdmin = configLocalMSP(conf, 'admin')
        # create tlscacerts directory and remove intermediatecerts if exists
        completeMSPSetup(org_admin_msp_dir)

    return enrollmentAdmin


def writeFile(filename, content):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, 'wb') as f:
        f.write(content)


def saveMSP(msp_dir, enrollment, admincerts=False):
    # cert
    filename = os.path.join(msp_dir, 'signcerts', 'cert.pem')
    writeFile(filename, enrollment._cert)

    # private key
    if enrollment._private_key:
        private_key = enrollment._private_key.private_bytes(encoding=serialization.Encoding.PEM,
                                                            format=serialization.PrivateFormat.PKCS8,
                                                            encryption_algorithm=serialization.NoEncryption())
        filename = os.path.join(msp_dir, 'keystore', 'key.pem')
        writeFile(filename, private_key)

    # ca
    filename = os.path.join(msp_dir, 'cacerts', 'ca.pem')
    writeFile(filename, enrollment._caCert)

    if admincerts:
        filename = os.path.join(msp_dir, 'admincerts', 'cert.pem')
        writeFile(filename, enrollment._cert)


def enrollWithFiles(user, org, msp_dir, csr=None, profile='', attr_reqs=None, admincerts=False):
    target = "https://%s:%s" % (org['ca']['host'], org['ca']['port']['internal'])
    cacli = ca_service(target=target,
                       ca_certs_path=org['ca']['certfile'],
                       ca_name=org['ca']['name'])
    enrollment = cacli.enroll(user['name'], user['pass'], csr=csr, profile=profile, attr_reqs=attr_reqs)

    saveMSP(msp_dir, enrollment, admincerts=admincerts)

    return enrollment


def genTLSCert(node, host_name, org, cert_file, key_file, ca_file):
    # Generate our key
    pkey = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend())

    # Generate a CSR
    csr = x509.CertificateSigningRequestBuilder().subject_name(x509.Name([
        # Provide various details about who we are.
        x509.NameAttribute(NameOID.COUNTRY_NAME, u"FR"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, u"Loire Atlantique"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, u"NAntes"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"owkin"),
        x509.NameAttribute(NameOID.COMMON_NAME, node['name']),
    ])).add_extension(
        x509.SubjectAlternativeName([
            # Describe what sites we want this certificate for.
            x509.DNSName(host_name),
        ]),
        critical=False,
        # Sign the CSR with our private key.
    ).sign(pkey, hashes.SHA256(), default_backend())

    target = "https://%s:%s" % (org['ca']['host'], org['ca']['port']['internal'])
    cacli = ca_service(target=target,
                       ca_certs_path=org['ca']['certfile'],
                       ca_name=org['ca']['name'])
    enrollment = cacli.enroll(node['name'], node['pass'], csr=csr, profile='tls')

    # cert
    writeFile(cert_file, enrollment._cert)

    # private key
    private_key = pkey.private_bytes(encoding=serialization.Encoding.PEM,
                                     format=serialization.PrivateFormat.PKCS8,
                                     encryption_algorithm=serialization.NoEncryption())
    writeFile(key_file, private_key)

    # ca
    writeFile(ca_file, enrollment._caCert)


def generateGenesis(conf):
    print('Generating orderer genesis block at %(genesis_bloc_file)s' % {
        'genesis_bloc_file': conf['misc']['genesis_bloc_file']['external']
    }, flush=True)

    # Note: For some unknown reason (at least for now) the block file can't be
    # named orderer.genesis.block or the orderer will fail to launch

    # configtxgen -profile OrgsOrdererGenesis -channelID substrasystemchannel -outputBlock /substra/data/genesis/genesis.block
    call(['configtxgen',
          '-profile', 'OrgsOrdererGenesis',
          '-channelID', conf['misc']['system_channel_name'],
          '-outputBlock', conf['misc']['genesis_bloc_file']['external']])
