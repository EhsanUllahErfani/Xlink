from pathlib import Path

from tablink.services.remote_desktop import (
    PASSWORD_ALPHABET,
    RDP_USER,
    RemoteDesktop,
    new_password,
    parse_password,
    parse_status,
)
from tests.fakes.fake_grdctl import FakeGrd

REAL_STATUS = (
    "Overall:\n\tUnit status: active\nRDP:\n\tStatus: enabled\n\tPort: 3389\n"
    "\tAuthentication methods: credentials\n\tTLS certificate: /x/tls.crt\n"
    "\tTLS fingerprint: 36:4d:89\n\tTLS key: /x/tls.key\n\tKerberos keytab: \n"
    "\tView-only: no\n\tNegotiate port: no\n\tUsername: tablink\n\tPassword: s3cret\n"
)


def make(fake: FakeGrd, tmp_path: Path) -> RemoteDesktop:
    return RemoteDesktop(runner=fake, tty=fake.tty, cert_dir=tmp_path / "grd")


def test_parse_real_status() -> None:
    st = parse_status(REAL_STATUS)
    assert st.ready and st.port == 3389 and st.username == "tablink"
    assert parse_password(REAL_STATUS) == "s3cret"


def test_parse_empty_and_hidden() -> None:
    text = REAL_STATUS.replace("tablink", "(empty)").replace("s3cret", "(empty)")
    assert parse_status(text).username == "" and parse_password(text) is None


def test_passwords_are_long_and_shell_safe() -> None:
    pws = {new_password() for _ in range(50)}
    assert len(pws) == 50
    assert all(len(p) == 24 and set(p) <= set(PASSWORD_ALPHABET) for p in pws)


async def test_setup_from_scratch(tmp_path: Path) -> None:
    fake = FakeGrd()
    st = await make(fake, tmp_path).setup()
    assert st.ready
    assert (tmp_path / "grd" / "tls.key").stat().st_mode & 0o777 == 0o600
    [(argv, lines)] = fake.tty_calls
    assert argv == ["grdctl", "rdp", "set-credentials"] and lines[0] == RDP_USER
    secret = lines[1]
    assert all(secret not in " ".join(c) for c in fake.calls)  # never on a command line


async def test_setup_is_idempotent(tmp_path: Path) -> None:
    fake = FakeGrd()
    rd = make(fake, tmp_path)
    await rd.setup()
    pw = fake.password
    await rd.setup()
    assert fake.password == pw and len(fake.tty_calls) == 1
    assert len([c for c in fake.calls if c[0] == "openssl"]) == 1


async def test_rotate_password(tmp_path: Path) -> None:
    fake = FakeGrd()
    rd = make(fake, tmp_path)
    await rd.setup()
    pw = fake.password
    await rd.setup(rotate_password=True)
    assert fake.password != pw
    assert await rd.password() == fake.password


async def test_real_tty_runner_types_into_prompt() -> None:
    from tablink.proc import tty_runner

    res = await tty_runner(["sh", "-c", "read a; read b; echo got-$a"], ["x1", "hidden"], 5)
    assert res.returncode == 0
    assert "hidden" not in res.stdout  # typed secrets are scrubbed from the output
