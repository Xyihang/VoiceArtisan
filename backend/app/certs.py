"""HTTPS 自签证书：首次启动自动生成（含 localhost / 本机 IP 的 SAN）。"""
from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import socket
from pathlib import Path

logger = logging.getLogger(__name__)

CERT_NAME = "cert.pem"
KEY_NAME = "key.pem"


def _local_ips() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        host = socket.gethostbyname(socket.gethostname())
        ips.add(host)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ips)


def ensure_certificate(cert_dir: Path, hostname: str | None = None) -> tuple[Path, Path]:
    """返回 (cert_path, key_path)，不存在时生成 10 年有效期的自签证书。"""
    cert_dir.mkdir(parents=True, exist_ok=True)
    cert_path = cert_dir / CERT_NAME
    key_path = cert_dir / KEY_NAME
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    common_name = hostname or socket.gethostname() or "localhost"
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "QWENTTS Local Dev"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )
    san_entries: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.DNSName(common_name),
    ]
    for ip in _local_ips():
        try:
            san_entries.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            continue
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    logger.info("已生成自签证书：%s（有效期 10 年）", cert_path)
    return cert_path, key_path


def help_text(port: int) -> str:
    return (
        "浏览器提示「您的连接不是私密连接」属于自签证书的正常现象。\n"
        "  处理方式：Chrome/Edge 点击「高级」→「继续前往（不安全）」；\n"
        f"  或先在浏览器中打开 https://localhost:{port}/api/health 完成一次信任；\n"
        "  生产环境请改用受信任证书或反向代理（Nginx/Caddy）。"
    )
