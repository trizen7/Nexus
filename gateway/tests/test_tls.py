import datetime as dt
import json
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from nexus_gateway.tls import TLSConfigurationError, TLSManager, TLSValidationError


def _fingerprint(path: Path) -> bytes:
    certificate = x509.load_pem_x509_certificate(path.read_bytes())
    return certificate.fingerprint(hashes.SHA256())


def _leaf_serial(path: Path) -> int:
    return x509.load_pem_x509_certificate(path.read_bytes()).serial_number


def _server_material(*, not_before: dt.datetime | None = None, not_after: dt.datetime | None = None) -> tuple[str, str]:
    now = dt.datetime.now(dt.timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Nexus Test"),
        x509.NameAttribute(NameOID.COMMON_NAME, "nexus.example.test"),
    ])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before or (now - dt.timedelta(hours=1)))
        .not_valid_after(not_after or (now + dt.timedelta(days=30)))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("nexus.example.test")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM).decode("ascii"),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii"),
    )


def test_bootstrap_generates_persistent_temporary_ca_and_server_certificate(tmp_path: Path):
    tls_dir = tmp_path / "tls"
    manager = TLSManager(tls_dir, bind_host="127.0.0.1", extra_hosts=["10.0.0.123"]).bootstrap()

    assert manager.mode == "temporary"
    for name in ("ca.key", "ca.crt", "server.key", "server.crt", "server-san.txt", "tls-state.json"):
        assert (tls_dir / name).is_file()
    status = manager.status()
    assert status["mode"] == "temporary"
    assert status["chain_length"] == 2
    assert status["temporary_ca_active"] is True
    assert status["temporary_ca_download_available"] is True
    assert status["ca_download_available"] is True
    assert "10.0.0.123" in status["ip_addresses"]
    assert "private_key" not in json.dumps(status)

    first_ca = _fingerprint(tls_dir / "ca.crt")
    TLSManager(tls_dir, bind_host="127.0.0.1", extra_hosts=["10.0.0.123"]).bootstrap()
    assert _fingerprint(tls_dir / "ca.crt") == first_ca


def test_san_change_rotates_only_leaf_certificate(tmp_path: Path):
    tls_dir = tmp_path / "tls"
    TLSManager(tls_dir, extra_hosts=["10.0.0.10"]).bootstrap()
    first_ca = _fingerprint(tls_dir / "ca.crt")
    first_leaf = _leaf_serial(tls_dir / "server.crt")

    manager = TLSManager(tls_dir, extra_hosts=["10.0.0.11"]).bootstrap()

    assert _fingerprint(tls_dir / "ca.crt") == first_ca
    assert _leaf_serial(tls_dir / "server.crt") != first_leaf
    assert "10.0.0.11" in manager.status()["ip_addresses"]


def test_incomplete_temporary_ca_is_never_silently_replaced(tmp_path: Path):
    tls_dir = tmp_path / "tls"
    tls_dir.mkdir()
    (tls_dir / "ca.crt").write_text("incomplete", encoding="ascii")

    with pytest.raises(TLSConfigurationError, match="ca.key"):
        TLSManager(tls_dir).bootstrap()


def test_existing_openssl_style_material_is_detected_without_state_file(tmp_path: Path):
    tls_dir = tmp_path / "tls"
    first = TLSManager(tls_dir, extra_hosts=["10.0.0.20"]).bootstrap()
    ca_fingerprint = _fingerprint(first.ca_certificate_path)
    first.state_path.unlink()

    restored = TLSManager(tls_dir, extra_hosts=["10.0.0.20"]).bootstrap()

    assert restored.mode == "temporary"
    assert _fingerprint(restored.ca_certificate_path) == ca_fingerprint


def test_custom_certificate_replaces_active_pair_and_preserves_ca(tmp_path: Path):
    manager = TLSManager(tmp_path / "tls").bootstrap()
    original_ca = manager.ca_certificate_path.read_bytes()
    certificate, private_key = _server_material()

    status = manager.replace_with_custom(certificate, private_key)

    assert status["mode"] == "custom"
    assert status["chain_length"] == 1
    assert status["temporary_ca_active"] is False
    assert status["temporary_ca_download_available"] is True
    assert status["ca_download_available"] is False
    assert manager.ca_certificate_path.read_bytes() == original_ca
    assert json.loads(manager.state_path.read_text(encoding="utf-8"))["mode"] == "custom"


def test_wrong_private_key_is_rejected_without_changing_active_certificate(tmp_path: Path):
    manager = TLSManager(tmp_path / "tls").bootstrap()
    original_certificate = manager.certificate_path.read_bytes()
    original_key = manager.private_key_path.read_bytes()
    certificate, _ = _server_material()
    _, wrong_key = _server_material()

    with pytest.raises(TLSValidationError, match="\u4e0d\u5339\u914d"):
        manager.replace_with_custom(certificate, wrong_key)

    assert manager.certificate_path.read_bytes() == original_certificate
    assert manager.private_key_path.read_bytes() == original_key
    assert manager.mode == "temporary"


@pytest.mark.parametrize(
    ("not_before", "not_after", "message"),
    [
        (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1), dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2), "\u5c1a\u672a\u751f\u6548"),
        (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2), dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1), "\u5df2\u8fc7\u671f"),
    ],
)
def test_custom_certificate_validity_window_is_enforced(tmp_path: Path, not_before: dt.datetime, not_after: dt.datetime, message: str):
    manager = TLSManager(tmp_path / "tls").bootstrap()
    certificate, private_key = _server_material(not_before=not_before, not_after=not_after)

    with pytest.raises(TLSValidationError, match=message):
        manager.replace_with_custom(certificate, private_key)


def test_hot_reload_failure_rolls_back_files_and_mode(tmp_path: Path):
    manager = TLSManager(tmp_path / "tls").bootstrap()
    original_certificate = manager.certificate_path.read_bytes()
    original_key = manager.private_key_path.read_bytes()
    original_state = manager.state_path.read_bytes()
    certificate, private_key = _server_material()

    class FailingOnceContext:
        def __init__(self) -> None:
            self.calls = 0

        def load_cert_chain(self, **_kwargs) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("simulated reload failure")

    context = FailingOnceContext()
    manager.ssl_context = context

    with pytest.raises(TLSConfigurationError, match="\u5df2\u6062\u590d"):
        manager.replace_with_custom(certificate, private_key)

    assert context.calls == 2
    assert manager.certificate_path.read_bytes() == original_certificate
    assert manager.private_key_path.read_bytes() == original_key
    assert manager.state_path.read_bytes() == original_state
    assert manager.mode == "temporary"
