from multiprocessing.pool import ThreadPool
from dataclasses import dataclass
from enum import Enum
from typing import Sequence
import socket
import struct

class Protocol(Enum):
    SSLv3 = b"\x03\x00"
    TLS_1_0 = b"\x03\x01"
    TLS_1_1 = b"\x03\x02"
    TLS_1_2 = b"\x03\x03"
    TLS_1_3 = b"\x03\x04"

class CipherSuite(Enum):
    # TLS 1.3 cipher suites.
    TLS_AES_128_GCM_SHA256 = b"\x13\x01"
    TLS_AES_256_GCM_SHA384 = b"\x13\x02"
    TLS_CHACHA20_POLY1305_SHA256 = b"\x13\x03"
    TLS_AES_128_CCM_SHA256 = b"\x13\x04"
    TLS_AES_128_CCM_8_SHA256 = b"\x13\x05"
    TLS_EMPTY_RENEGOTIATION_INFO_SCSV = b"\x00\xff"

    # TLS 1.2 and lower cipher suites.
    TLS_RSA_WITH_3DES_EDE_CBC_SHA = b"\x00\x0a"
    TLS_RSA_WITH_AES_128_CBC_SHA = b"\x00\x2f"
    TLS_RSA_WITH_AES_256_CBC_SHA = b"\x00\x35"
    TLS_RSA_WITH_AES_128_GCM_SHA256 = b"\x00\x9c"
    TLS_RSA_WITH_AES_256_GCM_SHA384 = b"\x00\x9d"
    TLS_ECDHE_ECDSA_WITH_AES_128_CBC_SHA = b"\xc0\x09"
    TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA = b"\xc0\x0a"
    TLS_ECDHE_RSA_WITH_3DES_EDE_CBC_SHA = b"\xc0\x12"
    TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA = b"\xc0\x13"
    TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA = b"\xc0\x14"
    TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256 = b"\xc0\x2b"
    TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384 = b"\xc0\x2c"
    TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256 = b"\xc0\x2f"
    TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384 = b"\xc0\x30"
    TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256 = b"\xcc\xa8"
    TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256 = b"\xcc\xa9"

class AlertLevel(Enum):
    """ Different alert levels that can be sent by the server. """
    WARNING = 1
    FATAL = 2

class AlertDescription(Enum):
    """ Different alert messages that can be sent by the server. """
    close_notify = 0
    unexpected_message = 10
    bad_record_mac = 20
    record_overflow = 22
    handshake_failure = 40
    bad_certificate = 42
    unsupported_certificate = 43
    certificate_revoked = 44
    certificate_expired = 45
    certificate_unknown = 46
    illegal_parameter = 47
    unknown_ca = 48
    access_denied = 49
    decode_error = 50
    decrypt_error = 51
    protocol_version = 70
    insufficient_security = 71
    internal_error = 80
    inappropriate_fallback = 86
    user_canceled = 90
    missing_extension = 109
    unsupported_extension = 110
    unrecognized_name = 112
    bad_certificate_status_response = 113
    unknown_psk_identity = 115
    certificate_required = 116
    no_application_protocol = 120

def to_uint24(n: int) -> bytes: return n.to_bytes(3, byteorder="big")
def to_uint8(n: int) -> bytes: return n.to_bytes(1, byteorder="big")
def to_uint16(n: int) -> bytes: return n.to_bytes(2, byteorder="big")
def from_uint8(b: bytes) -> int: return int.from_bytes(b, byteorder="big")
from_uint16 = from_uint8

class ServerError(Exception):
    def __init__(self, protocol: Protocol, level: AlertLevel, alert: AlertDescription):
        super().__init__(self, f'Server error ({protocol}): {level}: {alert}')
        self.protocol = protocol
        self.level = level
        self.alert = alert

@dataclass
class ServerHello:
    protocol: Protocol
    cipher_suite: CipherSuite

    @staticmethod
    def from_packet(packet: bytes):
        """
        Parses a Server Hello packet and returns the cipher suite accepted by the server.
        """
        if not packet:
            raise ValueError('Empty response')
        if packet[0] == 0x15:
            # Alert record
            record_type, legacy_record_version, length = struct.unpack('!BHH', packet[:5])
            assert record_type == 0x15
            alert_level_id, alert_description_id = struct.unpack('!BB', packet[5:7])
            raise ServerError(Protocol(to_uint16(legacy_record_version)), AlertLevel(alert_level_id), AlertDescription(alert_description_id))
        
        assert packet[0] == 0x16
        
        begin_format = "!BHHB3sH32sB"
        begin_length = struct.calcsize(begin_format)
        begin_packet = packet[:begin_length]
        (
            record_type,
            legacy_record_version,
            handshake_length,
            handshake_type,
            server_hello_length,
            server_version_int,
            server_random,
            session_id_length,
        ) = struct.unpack(begin_format, begin_packet)

        assert record_type == 0x16
        assert legacy_record_version in [0x0301, 0x0302, 0x0303]
        assert handshake_type == 0x02
        assert server_version_int in [0x0301, 0x0302, 0x0303]
        assert session_id_length in [0, 0x20]

        cipher_suite_start = begin_length+session_id_length
        cipher_suite_id = packet[cipher_suite_start:cipher_suite_start+2]
        # TODO: protocol is wrong for TLS 1.3 because it appears as an extension.
        return ServerHello(Protocol(to_uint16(server_version_int)), CipherSuite(cipher_suite_id))
    
@dataclass
class ClientHello:
    server_name: str
    allowed_protocols: Sequence[Protocol] = tuple(Protocol)
    allowed_cipher_suites: Sequence[CipherSuite] = tuple(CipherSuite)

    def make_packet(self) -> bytes:
        """
        Generates a Client Hello packet for the given server name and settings.
        """
        cipher_suites = b"".join(cipher_suite.value for cipher_suite in self.allowed_cipher_suites)

        curves = b"".join([
            b"\x00\x1d",  # Curve "x25519".
            b"\x00\x17",  # Curve "secp256r1".
            b"\x00\x1e",  # Curve "x448".
            b"\x00\x18",  # Curve "secp384r1".
            b"\x00\x19",  # Curve "secp521r1".
            b"\x01\x00",  # Curve "ffdhe2048".
            b"\x01\x01",  # Curve "ffdhe3072".
            b"\x01\x02",  # Curve "ffdhe4096".
            b"\x01\x03",  # Curve "ffdhe6144".
            b"\x01\x04",  # Curve "ffdhe8192".
        ])

        signature_algorithms = b"".join([
            b"\x04\x03", # ECDSA-SECP256r1-SHA256
            b"\x05\x03", # ECDSA-SECP384r1-SHA384
            b"\x06\x03", # ECDSA-SECP521r1-SHA512
            b"\x08\x07", # ED25519
            b"\x08\x08", # ED448
            b"\x08\x09", # RSA-PSS-PSS-SHA256
            b"\x08\x0a", # RSA-PSS-PSS-SHA384
            b"\x08\x0b", # RSA-PSS-PSS-SHA512
            b"\x08\x04", # RSA-PSS-RSAE-SHA256
            b"\x08\x05", # RSA-PSS-RSAE-SHA384
            b"\x08\x06", # RSA-PSS-RSAE-SHA512
            b"\x04\x01", # RSA-PKCS1-SHA256
            b"\x05\x01", # RSA-PKCS1-SHA384
            b"\x06\x01", # RSA-PKCS1-SHA512
            b"\x02\x01", # RSA-PKCS1-SHA1
            b"\x02\x03", # ECDSA-SHA1
        ])

        if Protocol.TLS_1_3 in self.allowed_protocols:
            # This extension is only available in TLS 1.3.
            supported_versions = b"".join(protocol.value for protocol in self.allowed_protocols)
            supported_version_extension = b"".join([
                b"\x00\x2b",  # Extension type: supported version.
                to_uint16(len(supported_versions)+1), # Length of extension data.
                to_uint8(len(supported_versions)), # Supported versions length.
                supported_versions
            ])
        else:
            supported_version_extension = b""

        extensions = b"".join([
            b"\x00\x00",  # Extension type: server_name.
            to_uint16(len(self.server_name) + 5),  # Length of extension data.
            to_uint16(len(self.server_name) + 3),  # Length of server_name list.
            b"\x00",  # Name type: host_name.
            to_uint16(len(self.server_name)),  # Length of host_name.
            self.server_name.encode("ascii"),

            b"\x00\x05", # Extension type: status_request. Allow server to send OCSP information.
            b"\x00\x05", # Length of extension data.
            b"\x01", # Certificate status type: OCSP.
            b"\x00\x00", # Responder ID list length.
            b"\x00\x00", # Request extension information length.

            b"\x00\x0b",  # Extension type: EC point formats.
            b"\x00\x04",  # Length of extension data.
            b"\x03",  # Length of EC point formats list.
            b"\x00",  # EC point format: uncompressed.
            b"\x01",  # EC point format: ansiX962_compressed_prime.
            b"\x02",  # EC point format: ansiX962_compressed_char2.

            b"\x00\x0a",  # Extension type: supported groups (mostly EC curves).
            to_uint16(len(curves) + 2),  # Length of extension data.
            to_uint16(len(curves)),  # Length of supported groups list.
            curves,

            b"\x00\x23",  # Extension type: session ticket.
            b"\x00\x00",  # No session ticket data follows.

            b"\x00\x16",  # Extension type: encrypt-then-MAC.
            b"\x00\x00",  # Length of extension data.

            b"\x00\x17",  # Extension type: extended master secret.
            b"\x00\x00",  # No extension data follows.

            b"\x00\x0d",  # Extension type: signature algorithms.
            to_uint16(len(signature_algorithms) + 2),  # Length of extension data.
            to_uint16(len(signature_algorithms)),  # Length of algorithm list.
            signature_algorithms,

            b"\xff\x01", # Extension type: renegotiation_info (TLS 1.2 or lower).
            b"\x00\x01", # Length of extension data.
            b"\x00", # Renegotiation info length.

            b"\x00\x12", # Extension type: SCT. Allow server to return signed certificate timestamp.
            b"\x00\x00", # Length of extension data.

            supported_version_extension, # Present only in TLS 1.3.

            # TODO: PSK key exchange modes extension.
            b"\x00\x2d\x00\x02\x01\x01",
            
            # TODO: key share extension.
            b"\x00\x33\x00\x26\x00\x24\x00\x1d\x00\x20\x35\x80\x72\xd6\x36\x58\x80\xd1\xae\xea\x32\x9a\xdf\x91\x21\x38\x38\x51\xed\x21\xa2\x8e\x3b\x75\xe9\x65\xd0\xd2\xcd\x16\x62\x54",
        ])

        client_hello_version = max(self.allowed_protocols, key=lambda protocol: protocol.value)
        if client_hello_version == Protocol.TLS_1_3:
            client_hello_version = Protocol.TLS_1_2
        client_hello = b"".join([
            client_hello_version.value,  # Legacy client version: max TLS 1.2 (because ossification).
            b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f",  # "Random".
            b"\x20",  # Legacy session ID length.
            b"\xe0\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xeb\xec\xed\xee\xef\xf0\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xfb\xfc\xfd\xfe\xff",  # Legacy session ID.
            to_uint16(len(cipher_suites)),
            cipher_suites,
            b"\x01",  # Legacy compression methods length.
            b"\x00",  # Legacy compression method: null.
            to_uint16(len(extensions)),
            extensions,
        ])

        handshake = b"".join([
            b"\x01",  # Handshake type: Client Hello.
            to_uint24(len(client_hello)),
            client_hello,
        ])

        record_version = Protocol.SSLv3 if client_hello_version == Protocol.SSLv3 else Protocol.TLS_1_0
        record = b"".join([
            b"\x16", # Record type: handshake.
            record_version.value, # Legacy record version: max TLS 1.0 (because ossification).
            to_uint16(len(handshake)),
            handshake,
        ])

        return record
    
    def send(self, port: int = 443, server_name: str | None = None, timeout: float | None = 2) -> ServerHello:
        """
        Sends a Client Hello packet to the server and returns the Server Hello packet.
        By default, sends the packet to the server specified in the constructor.
        """
        host = self.server_name if server_name is None else server_name
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.send(self.make_packet())
            return ServerHello.from_packet(s.recv(4096))

def _parse_host(host: str, port: int = 443):
    if ':' in host:
        server_name, port_str = host.split(':', 1)
        return server_name, int(port_str)
    else:
        return host, port

def enumerate_ciphers_suites(host: str, protocol: Protocol = Protocol.TLS_1_3, port: int = 443, max_workers: int = 1, timeout: float | None = 2) -> Sequence[CipherSuite]:
    """
    Enumerates the cipher suites accepted by the server.
    Since the server picks one accepted cipher suite from the list provided by the client,
    this function must repeatedly connect to the server until all acceptable cipher suites
    are found and the server refuses the handshake.
    """
    server_name, port = _parse_host(host, port)

    accepted_cipher_suites = []

    def enumerate_subset(remaining_cipher_suites: list[CipherSuite]) -> None:
        while True:
            client_hello = ClientHello(server_name, allowed_protocols=[protocol], allowed_cipher_suites=remaining_cipher_suites)
            try:
                server_hello = client_hello.send(port=port, timeout=timeout)
            except ServerError as e:
                if e.alert == AlertDescription.handshake_failure:
                    break
                else:
                    raise e
            accepted_cipher_suites.append(server_hello.cipher_suite)
            remaining_cipher_suites.remove(server_hello.cipher_suite)

    # Use % to distribute "desirable" cipher suites evenly.
    cipher_suite_parts = [[c for i, c in enumerate(CipherSuite) if i % max_workers == n] for n in range(max_workers)]
    with ThreadPool(max_workers) as pool:
        pool.map(enumerate_subset, cipher_suite_parts)
    
    if not accepted_cipher_suites:
        raise ValueError(f'Server did not accept any cipher suite. {protocol} is likely not supported')

    return accepted_cipher_suites

if __name__ == '__main__':
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else 'boppreh.com'
    from pprint import pprint
    pprint(enumerate_ciphers_suites(target, Protocol.TLS_1_3, max_workers=2))