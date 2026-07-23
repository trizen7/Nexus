from __future__ import annotations

import datetime as dt
import ipaddress
import json
import os
import secrets
import socket
import ssl
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

TLS_STATE_VERSION = 1
CUSTOM_CERTIFICATE_MAX_BYTES = 256 * 1024
CUSTOM_PRIVATE_KEY_MAX_BYTES = 64 * 1024


class TLSConfigurationError(RuntimeError):
    """Raised when the persisted TLS material is incomplete or unusable."""


class TLSValidationError(ValueError):
    """Raised when an uploaded certificate or private key is invalid."""


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _certificate_not_before(certificate: x509.Certificate) -> dt.datetime:
    value = getattr(certificate, "not_valid_before_utc", None)
    if value is not None:
        return value
    return certificate.not_valid_before.replace(tzinfo=dt.timezone.utc)


def _certificate_not_after(certificate: x509.Certificate) -> dt.datetime:
    value = getattr(certificate, "not_valid_after_utc", None)
    if value is not None:
        return value
    return certificate.not_valid_after.replace(tzinfo=dt.timezone.utc)


def _public_key_bytes(key: Any) -> bytes:
    return key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _atomic_write(path: Path, content: bytes, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            temporary.chmod(0o600 if private else 0o644)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n",
        private=False,
    )


def _normalize_pem(value: str, label: str, maximum: int) -> bytes:
    try:
        encoded = value.strip().encode("utf-8") + b"\n"
    except UnicodeEncodeError as exc:
        raise TLSValidationError(f"{label} 必须是有效的 UTF-8 PEM 文本") from exc
    if len(encoded) > maximum:
        raise TLSValidationError(f"{label} 超过允许的大小")
    return encoded


def _load_certificates(pem: bytes) -> list[x509.Certificate]:
    try:
        certificates = list(x509.load_pem_x509_certificates(pem))
    except ValueError as exc:
        raise TLSValidationError("证书 PEM 无效") from exc
    if not certificates:
        raise TLSValidationError("证书链不能为空")
    return certificates


def _load_private_key(pem: bytes) -> Any:
    try:
        return serialization.load_pem_private_key(pem, password=None)
    except (TypeError, ValueError) as exc:
        raise TLSValidationError("私钥 PEM 无效或包含密码") from exc


def _verify_signature(certificate: x509.Certificate, issuer: x509.Certificate) -> bool:
    try:
        public_key = issuer.public_key()
        if isinstance(public_key, rsa.RSAPublicKey):
            public_key.verify(
                certificate.signature,
                certificate.tbs_certificate_bytes,
                padding.PKCS1v15(),
                certificate.signature_hash_algorithm,
            )
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(
                certificate.signature,
                certificate.tbs_certificate_bytes,
                ec.ECDSA(certificate.signature_hash_algorithm),
            )
        else:
            return False
        return True
    except Exception:
        return False


def _certificate_names(certificate: x509.Certificate) -> tuple[list[str], list[str]]:
    dns_names: list[str] = []
    ip_addresses: list[str] = []
    try:
        extension = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns_names = sorted(set(extension.get_values_for_type(x509.DNSName)))
        ip_addresses = sorted(str(value) for value in set(extension.get_values_for_type(x509.IPAddress)))
    except x509.ExtensionNotFound:
        pass
    return dns_names, ip_addresses


def _name_text(name: x509.Name) -> str:
    return name.rfc4514_string() or "?"


def _fingerprint(certificate: x509.Certificate) -> str:
    raw = certificate.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(raw[index:index + 2] for index in range(0, len(raw), 2))


def _certificate_status(certificate: x509.Certificate, *, mode: str, chain_length: int) -> dict[str, Any]:
    dns_names, ip_addresses = _certificate_names(certificate)
    return {
        "configured": True,
        "mode": mode,
        "subject": _name_text(certificate.subject),
        "issuer": _name_text(certificate.issuer),
        "serial_number": format(certificate.serial_number, "X"),
        "not_before": _certificate_not_before(certificate).isoformat().replace("+00:00", "Z"),
        "not_after": _certificate_not_after(certificate).isoformat().replace("+00:00", "Z"),
        "sha256_fingerprint": _fingerprint(certificate),
        "dns_names": dns_names,
        "ip_addresses": ip_addresses,
        "chain_length": chain_length,
    }


def _local_certificate_hosts(bind_host: str, extra_hosts: Iterable[str]) -> tuple[list[str], list[ipaddress.IPv4Address | ipaddress.IPv6Address]]:
    names = {"localhost"}
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = {
        ipaddress.ip_address("127.0.0.1"),
        ipaddress.ip_address("::1"),
    }

    for candidate in (socket.gethostname(), socket.getfqdn(), bind_host, *extra_hosts):
        candidate = str(candidate or "").strip().strip("[]")
        if not candidate or candidate in {"0.0.0.0", "::", "*"}:
            continue
        try:
            addresses.add(ipaddress.ip_address(candidate))
        except ValueError:
            names.add(candidate)

    try:
        for family, _type, _proto, _canonname, sockaddr in socket.getaddrinfo(socket.gethostname(), None):
            if family in {socket.AF_INET, socket.AF_INET6}:
                address = str(sockaddr[0]).split("%", 1)[0]
                parsed = ipaddress.ip_address(address)
                if not parsed.is_unspecified:
                    addresses.add(parsed)
    except OSError:
        pass

    for probe in ("192.0.2.1", "198.51.100.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
                stream.connect((probe, 9))
                parsed = ipaddress.ip_address(stream.getsockname()[0])
                if not parsed.is_unspecified:
                    addresses.add(parsed)
        except OSError:
            pass

    return sorted(names), sorted(addresses, key=lambda value: (value.version, str(value)))


class TLSManager:
    """Owns generated/custom certificates and the live TLS context."""

    def __init__(self, tls_dir: Path, *, bind_host: str = "127.0.0.1", extra_hosts: Iterable[str] = ()) -> None:
        self.tls_dir = Path(tls_dir).expanduser().resolve()
        self.ca_key_path = self.tls_dir / "ca.key"
        self.ca_certificate_path = self.tls_dir / "ca.crt"
        self.certificate_path = self.tls_dir / "server.crt"
        self.private_key_path = self.tls_dir / "server.key"
        self.san_state_path = self.tls_dir / "server-san.txt"
        self.state_path = self.tls_dir / "tls-state.json"
        self.bind_host = bind_host
        self.extra_hosts = tuple(extra_hosts)
        self.mode = "temporary"
        self.ssl_context: ssl.SSLContext | None = None
        self._lock = threading.RLock()

    def bootstrap(self) -> "TLSManager":
        with self._lock:
            self.tls_dir.mkdir(parents=True, exist_ok=True)
            self.mode = self._detect_mode()
            if self.mode == "custom":
                self._validate_active_pair()
            else:
                self._ensure_temporary_material()
                self.mode = "temporary"
                self._write_state()
            self.ssl_context = self._new_context(self.certificate_path, self.private_key_path)
        return self

    def _read_state(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _detect_mode(self) -> str:
        saved_mode = str(self._read_state().get("mode", "")).strip().lower()
        if saved_mode in {"temporary", "custom"}:
            return saved_mode
        if self.certificate_path.is_file() and self.private_key_path.is_file():
            if self.ca_certificate_path.is_file() and self.ca_key_path.is_file():
                try:
                    leaf = _load_certificates(self.certificate_path.read_bytes())[0]
                    ca = _load_certificates(self.ca_certificate_path.read_bytes())[0]
                    if leaf.issuer == ca.subject and _verify_signature(leaf, ca):
                        return "temporary"
                except (OSError, TLSValidationError):
                    pass
            return "custom"
        return "temporary"

    def _write_state(self) -> None:
        _write_json(
            self.state_path,
            {
                "schema_version": TLS_STATE_VERSION,
                "mode": self.mode,
                "updated_at": _utcnow().isoformat().replace("+00:00", "Z"),
            },
        )

    def _ensure_temporary_material(self) -> None:
        ca_key_exists = self.ca_key_path.is_file()
        ca_certificate_exists = self.ca_certificate_path.is_file()
        if ca_key_exists != ca_certificate_exists:
            raise TLSConfigurationError(
                "临时 HTTPS CA 文件不完整：ca.key 与 ca.crt 必须同时存在。请恢复缺失文件，不会自动替换已有 CA"
            )
        if not ca_key_exists:
            self._create_ca()

        try:
            ca_key = serialization.load_pem_private_key(self.ca_key_path.read_bytes(), password=None)
            ca_certificate = _load_certificates(self.ca_certificate_path.read_bytes())[0]
        except (OSError, TypeError, ValueError, TLSValidationError) as exc:
            raise TLSConfigurationError("临时 HTTPS CA 无法读取") from exc
        if _public_key_bytes(ca_key.public_key()) != _public_key_bytes(ca_certificate.public_key()):
            raise TLSConfigurationError("临时 HTTPS CA 证书与私钥不匹配")
        try:
            constraints = ca_certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
            if not constraints.ca:
                raise TLSConfigurationError("临时 HTTPS CA 证书不是 CA 证书")
        except x509.ExtensionNotFound as exc:
            raise TLSConfigurationError("临时 HTTPS CA 证书缺少 CA 约束") from exc

        names, addresses = _local_certificate_hosts(self.bind_host, self.extra_hosts)
        if not self._temporary_server_is_current(ca_certificate, names, addresses):
            self._create_server_certificate(ca_key, ca_certificate, names, addresses)

    def _create_ca(self) -> None:
        now = _utcnow()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        subject = x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Nexus"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Nexus Temporary Local CA"),
        ])
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
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
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()), critical=False)
            .sign(private_key=private_key, algorithm=hashes.SHA256())
        )
        _atomic_write(
            self.ca_key_path,
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            private=True,
        )
        _atomic_write(self.ca_certificate_path, certificate.public_bytes(serialization.Encoding.PEM))

    def _temporary_server_is_current(
        self,
        ca_certificate: x509.Certificate,
        names: list[str],
        addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
    ) -> bool:
        if not self.certificate_path.is_file() or not self.private_key_path.is_file():
            return False
        try:
            certificates = _load_certificates(self.certificate_path.read_bytes())
            certificate = certificates[0]
            private_key = serialization.load_pem_private_key(self.private_key_path.read_bytes(), password=None)
        except (OSError, TypeError, ValueError, TLSValidationError):
            return False
        if _public_key_bytes(private_key.public_key()) != _public_key_bytes(certificate.public_key()):
            return False
        if certificate.issuer != ca_certificate.subject or not _verify_signature(certificate, ca_certificate):
            return False
        if _certificate_not_after(certificate) <= _utcnow() + dt.timedelta(days=7):
            return False
        current_names, current_addresses = _certificate_names(certificate)
        return set(names).issubset(current_names) and {str(value) for value in addresses}.issubset(current_addresses)

    def _create_server_certificate(
        self,
        ca_key: Any,
        ca_certificate: x509.Certificate,
        names: list[str],
        addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address],
    ) -> None:
        now = _utcnow()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Nexus"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Nexus Temporary Local Gateway"),
        ])
        san_values: list[x509.GeneralName] = [x509.DNSName(value) for value in names]
        san_values.extend(x509.IPAddress(value) for value in addresses)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_certificate.subject)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(hours=1))
            .not_valid_after(now + dt.timedelta(days=825))
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
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName(san_values), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(private_key=ca_key, algorithm=hashes.SHA256())
        )
        _atomic_write(
            self.private_key_path,
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            private=True,
        )
        _atomic_write(
            self.certificate_path,
            certificate.public_bytes(serialization.Encoding.PEM)
            + ca_certificate.public_bytes(serialization.Encoding.PEM),
        )
        _atomic_write(
            self.san_state_path,
            (",".join([*(f"DNS:{value}" for value in names), *(f"IP:{value}" for value in addresses)]) + "\n").encode("utf-8"),
        )

    def _validate_active_pair(self) -> None:
        if not self.certificate_path.is_file() or not self.private_key_path.is_file():
            raise TLSConfigurationError("正式 HTTPS 证书链或私钥文件缺失")
        try:
            certificate_pem = self.certificate_path.read_bytes()
            private_key_pem = self.private_key_path.read_bytes()
            certificates = _load_certificates(certificate_pem)
            private_key = _load_private_key(private_key_pem)
        except (OSError, TLSValidationError) as exc:
            raise TLSConfigurationError("正式 HTTPS 证书或私钥无法读取") from exc
        if _public_key_bytes(private_key.public_key()) != _public_key_bytes(certificates[0].public_key()):
            raise TLSConfigurationError("正式 HTTPS 证书与私钥不匹配")
        self._new_context(self.certificate_path, self.private_key_path)

    @staticmethod
    def _new_context(certificate_path: Path, private_key_path: Path) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=str(certificate_path), keyfile=str(private_key_path))
        return context

    def status(self) -> dict[str, Any]:
        with self._lock:
            certificates = _load_certificates(self.certificate_path.read_bytes())
            payload = _certificate_status(certificates[0], mode=self.mode, chain_length=len(certificates))
            temporary_ca_available = self.ca_certificate_path.is_file()
            payload["temporary_ca_active"] = self.mode == "temporary"
            payload["temporary_ca_download_available"] = temporary_ca_available
            payload["ca_download_available"] = temporary_ca_available and self.mode == "temporary"
            payload["hot_reload_supported"] = True
            return payload

    def replace_with_custom(self, certificate_chain: str, private_key: str) -> dict[str, Any]:
        certificate_pem = _normalize_pem(certificate_chain, "证书链", CUSTOM_CERTIFICATE_MAX_BYTES)
        private_key_pem = _normalize_pem(private_key, "私钥", CUSTOM_PRIVATE_KEY_MAX_BYTES)
        certificates = _load_certificates(certificate_pem)
        loaded_private_key = _load_private_key(private_key_pem)
        leaf = certificates[0]
        if _public_key_bytes(loaded_private_key.public_key()) != _public_key_bytes(leaf.public_key()):
            raise TLSValidationError("证书与私钥不匹配")
        now = _utcnow()
        if _certificate_not_before(leaf) > now:
            raise TLSValidationError("证书尚未生效")
        if _certificate_not_after(leaf) <= now:
            raise TLSValidationError("证书已过期")

        with tempfile.TemporaryDirectory(prefix="nexus-tls-check-") as temporary_directory:
            temporary_root = Path(temporary_directory)
            temporary_certificate = temporary_root / "server.crt"
            temporary_key = temporary_root / "server.key"
            temporary_certificate.write_bytes(certificate_pem)
            temporary_key.write_bytes(private_key_pem)
            try:
                self._new_context(temporary_certificate, temporary_key)
            except (OSError, ssl.SSLError) as exc:
                raise TLSValidationError("证书链或私钥无法用于 TLS，请确认 PEM 顺序、算法和密码设置") from exc

        with self._lock:
            if self.ssl_context is None:
                raise TLSConfigurationError("TLS 上下文尚未初始化")
            backup_certificate = self.certificate_path.with_suffix(self.certificate_path.suffix + ".bak")
            backup_key = self.private_key_path.with_suffix(self.private_key_path.suffix + ".bak")
            backup_state = self.state_path.with_suffix(self.state_path.suffix + ".bak")
            old_mode = self.mode
            old_certificate = self.certificate_path.read_bytes()
            old_key = self.private_key_path.read_bytes()
            old_state = self.state_path.read_bytes() if self.state_path.is_file() else b""
            _atomic_write(backup_certificate, old_certificate)
            _atomic_write(backup_key, old_key, private=True)
            if old_state:
                _atomic_write(backup_state, old_state)

            try:
                _atomic_write(self.certificate_path, certificate_pem)
                _atomic_write(self.private_key_path, private_key_pem, private=True)
                self.mode = "custom"
                self._write_state()
                self.ssl_context.load_cert_chain(
                    certfile=str(self.certificate_path),
                    keyfile=str(self.private_key_path),
                )
            except Exception as exc:
                _atomic_write(self.certificate_path, old_certificate)
                _atomic_write(self.private_key_path, old_key, private=True)
                self.mode = old_mode
                if old_state:
                    _atomic_write(self.state_path, old_state)
                else:
                    self._write_state()
                self.ssl_context.load_cert_chain(
                    certfile=str(self.certificate_path),
                    keyfile=str(self.private_key_path),
                )
                raise TLSConfigurationError("证书热更新失败，已恢复原证书") from exc
            return self.status()
