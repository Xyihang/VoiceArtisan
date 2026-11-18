"""回归：保存新密码必须覆盖旧密文（否则换过密码的云主机永远连不上）。"""
from pathlib import Path

from app import cloud_hosts


def test_new_password_overrides_old(tmp_path: Path):
    cloud_hosts.save(tmp_path, {"host": "h1", "ssh_port": 22, "username": "root", "password": "OLD"})
    assert cloud_hosts.load(tmp_path, decrypt=True)["password"] == "OLD"

    cloud_hosts.save(tmp_path, {"password": "NEW"})
    assert cloud_hosts.load(tmp_path, decrypt=True)["password"] == "NEW"

    # 留空 = 沿用旧密码
    cloud_hosts.save(tmp_path, {"host": "h1"})
    assert cloud_hosts.load(tmp_path, decrypt=True)["password"] == "NEW"


def test_empty_password_keeps_previous(tmp_path: Path):
    cloud_hosts.save(tmp_path, {"host": "h1", "username": "root", "password": "KEEP"})
    cloud_hosts.save(tmp_path, {"password": ""})
    assert cloud_hosts.load(tmp_path, decrypt=True)["password"] == "KEEP"
