"""Component lifecycle tests use tiny fixtures; real wheels are checked separately."""
from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import platform
import sys
import tarfile
from urllib.error import URLError

import pytest
from typer.testing import CliRunner

from strathive import settings, schedule
from strathive.cli import app
from strathive.core.rules import LedgerError
from strathive.data.history import COMMIT, component
from strathive.data.history.bundle import RECEIPT, digest, manifest, unpack
from strathive.data.history.provider import History, configuration
from strathive.data.service import Data


def bundle(tmp_path, members=None):
    members = members or {'wheels/example-1-py3-none-any.whl': b'fixture',
                         'requirements.lock': b'example==1 --hash=sha256:' + b'0' * 64 + b'\n'}
    path = tmp_path / 'fixture.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    value = {'format': 1, 'component': 'history', 'commit': COMMIT, 'cnequity_version': '0.25.0',
             'python': f'{sys.version_info.major}.{sys.version_info.minor}',
             'platform': f'{sys.platform}_{platform.machine()}',
             'artifact': {'sha256': digest(path), 'url': 'https://example.test/history.tar.gz'}}
    file = tmp_path / 'manifest.json'
    file.write_text(json.dumps(value))
    return path, file, value


@pytest.fixture
def installer(monkeypatch):
    calls = []
    def run(arguments, **kwargs):
        if str(arguments[3]) == 'venv':
            directory = Path(arguments[-1])
            (directory / 'bin').mkdir(parents=True)
            (directory / 'bin/python').write_text('fixture interpreter')
        calls.append(list(map(str, arguments)))
    def call(self, action, **arguments):
        calls.append(action)
        if action == 'init':
            root = Path(self.config['root'])
            (root / 'lake').mkdir(parents=True, exist_ok=True)
            (root / 'lake/catalog.sqlite').write_bytes(b'fixture catalog retained across uninstall')
        return {'passed': True}
    monkeypatch.setattr(component, 'run', run)
    monkeypatch.setattr(History, 'call', call)
    return calls


def test_install_is_optional_and_uninstall_reinstall_retains_data(tmp_path, installer):
    archive, file, _ = bundle(tmp_path)
    ledger = tmp_path / 'ledger.sqlite3'
    ledger.write_bytes(b'ledger facts')
    settings.save({'ledger': str(ledger), 'source': 'galaxy', 'providers': {'galaxy': {}},
                   'selection': {'quote': ['galaxy'], 'close': ['galaxy'], 'calendar': ['galaxy']}})
    first = component.install(bundle=archive, manifest_file=file)
    assert first['installed'] and not first['sync_started'] and not first['timers_enabled']
    python = Path(first['python'])
    assert component.managed(python.parent.parent)
    assert History().status()['installed'] and History().status()['managed']
    assert settings.settings()['selection']['close'] == ['galaxy', 'cnequity']
    assert settings.settings()['selection']['quote'] == ['galaxy']
    assert component.install(bundle=archive, manifest_file=file)['reused']
    root = Path(first['data_root'])
    data = root / 'lake/catalog.sqlite'
    original = data.read_bytes()
    removed = component.uninstall()
    assert removed['data_retained'] and not python.exists()
    assert not History().status()['installed'] and not History().status()['enabled']
    assert settings.settings()['selection']['close'] == ['galaxy']
    assert 'cnequity' not in Data().providers
    assert data.read_bytes() == original and ledger.read_bytes() == b'ledger facts'
    second = component.install(bundle=archive, manifest_file=file)
    assert second['installed'] and data.read_bytes() == original
    assert installer.count('init') == 1
    assert installer.count('selftest') == 3
    assert component.uninstall()['uninstalled']
    assert component.uninstall()['uninstalled']


def test_repair_failure_preserves_working_environment_and_config(tmp_path, installer, monkeypatch):
    archive, file, _ = bundle(tmp_path)
    first = component.install(bundle=archive, manifest_file=file)
    before = settings.settings()
    python = Path(first['python'])
    original = History.call
    def failed(self, action, **kwargs):
        if action == 'selftest':
            raise LedgerError('history_selftest_failed', 'fixture failure')
        return original(self, action, **kwargs)
    monkeypatch.setattr(History, 'call', failed)
    with pytest.raises(LedgerError, match='fixture failure'):
        component.install(bundle=archive, manifest_file=file, repair=True)
    assert settings.settings() == before and python.exists()
    assert [p for p in (component.home() / 'environments').iterdir()] == [python.parent.parent]
    assert not list(component.home().glob('.install-*'))


def test_uninstall_retains_user_supplied_python_and_unowned_files(tmp_path):
    external = tmp_path / 'external/bin/python'
    external.parent.mkdir(parents=True)
    external.write_text('user-owned')
    config = configuration({'history': {'python': str(external)}})
    settings.save({'history': config, 'selection': {'close': ['cnequity'], 'calendar': ['cnequity']}})
    directory = component.home() / 'environments/unowned'
    directory.mkdir(parents=True)
    (directory / 'keep').write_text('keep')
    result = component.uninstall()
    assert external.is_file() and (directory / 'keep').is_file()
    assert result['external_environment_retained']
    assert settings.settings()['selection']['calendar'] == []


def test_busy_component_refuses_removal_and_stopped_timer_failure_keeps_environment(tmp_path, installer, monkeypatch):
    archive, file, _ = bundle(tmp_path)
    installed = component.install(bundle=archive, manifest_file=file)
    with component.access():
        with pytest.raises(LedgerError) as error:
            component.uninstall()
        assert error.value.code == 'history_component_busy'
    def failed():
        raise LedgerError('history_schedule_stop_failed', 'cannot stop timer')
    monkeypatch.setattr(schedule, 'disable_history', failed)
    with pytest.raises(LedgerError, match='cannot stop timer'):
        component.uninstall()
    assert Path(installed['python']).is_file() and History().status()['enabled']


def test_download_retries_then_reuses_validated_cache(tmp_path, monkeypatch):
    archive, _, value = bundle(tmp_path)
    calls = []
    class Response(io.BytesIO):
        pass
    def get(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise URLError('fixture disconnect')
        return Response(archive.read_bytes())
    monkeypatch.setattr(component, 'urlopen', get)
    target = component.download(value, lambda _: None)
    assert target.read_bytes() == archive.read_bytes() and len(calls) == 2
    assert component.download(value, lambda _: None) == target and len(calls) == 2
    assert not list(target.parent.glob('*.part'))


def test_bad_checksum_or_bad_archive_cannot_change_configuration(tmp_path, installer):
    archive, file, value = bundle(tmp_path)
    value['artifact']['sha256'] = 'f' * 64
    file.write_text(json.dumps(value))
    before = settings.settings()
    with pytest.raises(LedgerError) as error:
        component.install(bundle=archive, manifest_file=file)
    assert error.value.code == 'history_checksum_failed'
    assert settings.settings() == before
    assert not list((component.home() / 'environments').iterdir())
    archive.write_bytes(b'not a tar archive')
    value['artifact']['sha256'] = digest(archive)
    with pytest.raises(LedgerError) as error:
        unpack(archive, tmp_path / 'extracted', value)
    assert error.value.code == 'history_bundle_invalid'


@pytest.mark.parametrize('name', ['../escape', '/tmp/escape', 'wheels/../../escape', 'setup.py'])
def test_bundle_rejects_unexpected_files(tmp_path, name):
    archive, _, value = bundle(tmp_path, {name: b'x'})
    with pytest.raises(LedgerError) as error:
        unpack(archive, tmp_path / 'extracted', value)
    assert error.value.code == 'history_bundle_invalid'
    assert not (tmp_path / 'escape').exists()


def test_optional_cli_install_and_uninstall_never_initializes_ledger(tmp_path, installer):
    archive, file, _ = bundle(tmp_path)
    ledger = tmp_path / 'absent.db'
    runner = CliRunner()
    result = runner.invoke(app, ['--db', str(ledger), 'data', 'history', 'install', '--manifest', str(file), '--bundle', str(archive)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)['data']['installed']
    assert not ledger.exists()
    removed = runner.invoke(app, ['--db', str(ledger), 'data', 'history', 'uninstall'])
    assert removed.exit_code == 0 and json.loads(removed.stdout)['data']['data_retained']
    assert not ledger.exists()


def test_setup_can_skip_or_install_optional_component(tmp_path, monkeypatch):
    from strathive.cli.setup import offer_history
    calls = []
    monkeypatch.setattr(component, 'install', lambda **kwargs: calls.append('install') or {'data_root': str(tmp_path)})
    wrapper = __import__('typer').Typer()
    wrapper.command()(offer_history)
    runner = CliRunner()
    assert runner.invoke(wrapper, [], input='n\n').exit_code == 0
    assert calls == []
    assert runner.invoke(wrapper, [], input='y\n').exit_code == 0
    assert calls == ['install']


def test_setup_install_failure_keeps_ledger_usable(monkeypatch):
    from strathive.cli.setup import offer_history
    def fail(**kwargs):
        raise LedgerError('history_download_failed', 'fixture offline')
    monkeypatch.setattr(component, 'install', fail)
    wrapper = __import__('typer').Typer()
    wrapper.command()(offer_history)
    result = CliRunner().invoke(wrapper, [], input='y\n')
    assert result.exit_code == 0 and '账本和原配置保持可用' in result.output


def test_runtime_change_prepares_new_environment_before_switching(tmp_path, installer):
    archive, file, _ = bundle(tmp_path)
    first = component.install(bundle=archive, manifest_file=file)
    python = Path(first['python'])
    record_path = python.parent.parent / RECEIPT
    record = json.loads(record_path.read_text())
    record['python'] = '3.14.0'
    record_path.write_text(json.dumps(record))
    second = component.install(bundle=archive, manifest_file=file)
    assert not second['reused'] and second['python'] != first['python']
    assert python.exists() and settings.settings()['history']['python'] == second['python']
    assert len(component.uninstall()['removed_environments']) == 2


def test_low_install_disk_space_keeps_configuration(tmp_path, monkeypatch):
    from types import SimpleNamespace
    archive, file, _ = bundle(tmp_path)
    before = settings.settings()
    monkeypatch.setattr(component.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1024))
    with pytest.raises(LedgerError) as error:
        component.install(bundle=archive, manifest_file=file)
    assert error.value.code == 'history_install_low_space'
    assert settings.settings() == before and not (component.home() / 'environments').exists()


def test_disable_history_stops_only_its_own_units(tmp_path, monkeypatch):
    directory = settings.config_dir().parent / 'systemd/user'
    directory.mkdir(parents=True)
    for extension in ('service', 'timer'):
        (directory / f'strathive-history-daily.{extension}').write_text('fixture')
    reference = directory / 'strathive-security-sync.timer'
    reference.write_text('reference sync retained')
    calls = []
    monkeypatch.setattr(schedule.subprocess, 'run', lambda args, **kw: calls.append(args))
    schedule.disable_history()
    assert reference.exists() and not list(directory.glob('strathive-history-*'))
    assert calls == [
        ['systemctl', '--user', 'disable', '--now', 'strathive-history-daily.timer'],
        ['systemctl', '--user', 'stop', 'strathive-history-daily.service'],
        ['systemctl', '--user', 'daemon-reload']]
