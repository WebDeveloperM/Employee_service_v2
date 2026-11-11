"""Client-certificate (mTLS) key issuance for employee login.

The service keeps a single self-signed root CA on disk (see PKI_DIR). Every
employee ".pfx" key is a personal certificate + private key signed by that
CA and bundled into a password-protected PKCS#12 file. When the CA's public
certificate is installed as a trusted client CA in nginx (ssl_client_certificate),
and the employee's .pfx is installed into their browser/OS certificate store,
their browser presents it during the TLS handshake and nginx forwards the
verified serial number to Django (see middleware.ClientCertificateAuthenticationMiddleware).

The CA private key never leaves this server and is not included in any .pfx.
"""
import datetime
import os
import secrets
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from django.conf import settings
from django.utils import timezone


CA_KEY_FILENAME = 'ca.key'
CA_CERT_FILENAME = 'ca.crt'
RSA_KEY_SIZE = 2048
CA_RSA_KEY_SIZE = 4096
CA_VALIDITY_DAYS = 3650
DEFAULT_CERT_VALIDITY_DAYS = 730
DEFAULT_PASSWORD_LENGTH = 14
PASSWORD_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'


def get_pki_dir() -> Path:
    pki_dir = Path(getattr(settings, 'PKI_DIR', settings.BASE_DIR / 'pki'))
    pki_dir.mkdir(parents=True, exist_ok=True)
    return pki_dir


def normalize_serial_hex(value) -> str:
    text = str(value or '').strip().upper().replace(':', '').replace(' ', '')
    text = text.lstrip('0')
    return text or '0'


def _generate_ca():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=CA_RSA_KEY_SIZE)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, 'Employee Service Root CA'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Employee Service'),
    ])
    now = datetime.datetime.utcnow()

    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=CA_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )
    return private_key, certificate


def get_or_create_ca():
    pki_dir = get_pki_dir()
    key_path = pki_dir / CA_KEY_FILENAME
    cert_path = pki_dir / CA_CERT_FILENAME

    if key_path.exists() and cert_path.exists():
        private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
        return private_key, certificate

    private_key, certificate = _generate_ca()
    key_path.write_bytes(private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return private_key, certificate


def _generate_password(length=DEFAULT_PASSWORD_LENGTH) -> str:
    return ''.join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def generate_employee_pfx(employee, *, validity_days=None):
    """Issue a brand-new personal certificate + key for `employee` and bundle
    it into a password-protected PKCS#12 (.pfx) file.

    Returns a dict with pfx_bytes, password, serial_number (matches nginx's
    $ssl_client_serial format once normalized), fingerprint_sha256 and
    expires_at (tz-aware, for the EmployeeCertificate record).
    """
    validity_days = validity_days or int(getattr(settings, 'EMPLOYEE_CERT_VALIDITY_DAYS', DEFAULT_CERT_VALIDITY_DAYS))
    ca_key, ca_cert = get_or_create_ca()

    employee_key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_SIZE)

    common_name = f'{employee.tabel_number} - {employee}'.strip()[:64]
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name or employee.tabel_number),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, employee.slug or employee.tabel_number),
    ])

    now = datetime.datetime.utcnow()
    serial_number = x509.random_serial_number()
    not_after = now + datetime.timedelta(days=validity_days)

    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(employee_key.public_key())
        .serial_number(serial_number)
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    password = _generate_password()
    pfx_bytes = pkcs12.serialize_key_and_certificates(
        name=employee.tabel_number.encode('utf-8'),
        key=employee_key,
        cert=certificate,
        cas=[ca_cert],
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode('utf-8')),
    )

    return {
        'pfx_bytes': pfx_bytes,
        'password': password,
        'serial_number': normalize_serial_hex(format(serial_number, 'X')),
        'fingerprint_sha256': certificate.fingerprint(hashes.SHA256()).hex(),
        'expires_at': timezone.now() + datetime.timedelta(days=validity_days),
    }
