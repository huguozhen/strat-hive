"""Install/remove optional history software; historical data has its own lifecycle."""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import fcntl
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from time import monotonic
from urllib.request import urlopen
import uuid

from strathive import settings
from strathive.core.rules import LedgerError, require
from .bundle import RECEIPT, digest, manifest, unpack


def home():
    return settings.data_dir() / 'components/history'


@contextmanager
def access(exclusive=False):
    directory = home().parent
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'history.lock').open('a') as lock:
        try:
            fcntl.flock(lock, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LedgerError('history_component_busy', '历史组件正在运行或安装，请稍后重试。') from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def receipt(python):
    try:
        value = json.loads((Path(python).parent.parent / RECEIPT).read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def managed(environment):
    directory = Path(environment)
    if directory.is_symlink() or directory.parent.resolve() != (home() / 'environments').resolve():
        return False
    value = receipt(directory / 'bin/python') or {}
    return value.get('managed_by') == 'strathive' and value.get('component') == 'history'


def run(arguments, *, timeout=900, progress=None):
    # Keep stdout/stderr out of the JSON protocol; show phase progress separately.
    try:
        result = subprocess.run(list(map(str, arguments)), capture_output=True, text=True,
                                timeout=timeout, env={**os.environ, 'PYTHONNOUSERSITE': '1'})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LedgerError('history_install_failed', '历史组件安装进程失败或超时；可重新执行安装。') from exc
    require(result.returncode == 0, '历史组件安装失败：' + (result.stderr or result.stdout)[-1800:], 'history_install_failed')
    return result.stdout


def download(value, progress):
    artifact = value.get('artifact')
    require(isinstance(artifact, dict) and isinstance(artifact.get('sha256'), str) and len(artifact['sha256']) == 64,
            '本构建尚无历史组件制品；请使用正式发布包，或传入构建生成的 --manifest 和 --bundle。', 'history_artifact_unavailable')
    cache = home() / 'downloads'
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (artifact['sha256'] + '.tar.gz')
    if target.is_file() and digest(target) == artifact['sha256']:
        progress('复用已校验的历史组件下载缓存。')
        return target
    require(str(artifact.get('url', '')).startswith('https://'), '历史组件下载地址须为 HTTPS。', 'history_bundle_invalid')
    temporary = target.with_suffix('.part')
    for attempt in range(3):
        try:
            total, reported, started = 0, 0, monotonic()
            progress(f'正在下载历史组件（第 {attempt + 1}/3 次）。')
            with urlopen(artifact['url'], timeout=20) as response, temporary.open('wb') as stream:
                while chunk := response.read(1024**2):
                    total += len(chunk)
                    require(total <= 1024**3 and monotonic() - started < 600,
                            '历史组件下载超时或超过大小限制。', 'history_download_failed')
                    stream.write(chunk)
                    if total - reported >= 16 * 1024**2:
                        progress(f'历史组件已下载 {total // 1024**2} MiB。')
                        reported = total
            require(digest(temporary) == artifact['sha256'], '历史组件 SHA-256 校验失败。', 'history_checksum_failed')
            os.replace(temporary, target)
            return target
        except OSError as exc:
            if attempt == 2:
                raise LedgerError('history_download_failed', '历史组件下载失败；可重试或使用离线制品。') from exc
        finally:
            temporary.unlink(missing_ok=True)


def activate(config):
    from strathive.data.service import configuration
    values = configuration(settings.settings())
    values['history'] = {**config, 'enabled': True}
    for capability in ('close', 'calendar'):
        values['selection'][capability] = list(dict.fromkeys([*values['selection'].get(capability, []), 'cnequity']))
    settings.save(values)


def install(*, bundle=None, manifest_file=None, repair=False, progress=lambda _: None):
    from .provider import History, configuration
    from .sync import writable
    value = manifest(manifest_file)
    artifact = value.get('artifact')
    require(isinstance(artifact, dict), '本构建尚未提供历史组件发布制品。', 'history_artifact_unavailable')
    with access(exclusive=True):
        require(shutil.disk_usage(home().parent).free >= 2 * 1024**3,
                '安装历史组件需要至少 2GiB 临时工作空间。', 'history_install_low_space')
        config = configuration()
        config['enabled'] = True
        writable(config['root'], config)
        current = receipt(config['python']) or {}
        same_runtime = current.get('python') == sys.version.split()[0] and current.get('base_prefix') == sys.base_prefix
        if not repair and same_runtime and current.get('artifact_sha256') == artifact.get('sha256') and Path(config['python']).is_file():
            backend = History(config)
            backend.call('doctor', _locked=True)
            backend.call('selftest', _locked=True)
            backend.call('validate' if backend.status()['initialized'] else 'init', _locked=True)
            activate(config)
            return {'installed': True, 'reused': True, 'python': config['python'], 'data_root': config['root']}
        archive = Path(bundle).expanduser().absolute() if bundle else download(value, progress)
        environments = home() / 'environments'
        environments.mkdir(parents=True, exist_ok=True)
        destination = environments / (artifact['sha256'][:12] + '-' + uuid.uuid4().hex[:8])
        previous_python = config['python']
        completed = False
        try:
            with tempfile.TemporaryDirectory(prefix='.install-', dir=home()) as temporary:
                source = unpack(archive, Path(temporary) / 'bundle', value)
                progress('正在创建独立 Python 环境并安装锁定依赖。')
                # Keep the final path stable: venv console scripts embed absolute shebangs.
                # Activation is the later atomic settings update, never moving the venv.
                candidate = destination
                run([sys.executable, '-E', '-m', 'venv', candidate])
                python = candidate / 'bin/python'
                run([python, '-E', '-m', 'pip', '--isolated', '--disable-pip-version-check', 'install',
                     '--no-cache-dir', '--no-index', '--find-links', source / 'wheels', '--only-binary=:all:',
                     '--require-hashes', '-r', source / 'requirements.lock'])
                run([python, '-E', '-m', 'pip', '--isolated', 'check'])
                record = {**{k: value[k] for k in ('format', 'component', 'commit', 'cnequity_version')},
                          'managed_by': 'strathive', 'artifact_sha256': artifact['sha256'],
                          'python': sys.version.split()[0], 'base_prefix': sys.base_prefix,
                          'installed_at': datetime.now(timezone.utc).isoformat()}
                (candidate / RECEIPT).write_text(json.dumps(record))
                config['python'] = str(destination / 'bin/python')
                backend = History(config)
                progress('正在检查依赖、DuckDB 扩展和离线存储样本。')
                backend.call('doctor', _locked=True)
                backend.call('selftest', _locked=True)
                backend.call('validate' if backend.status()['initialized'] else 'init', _locked=True)
                activate(config)
                completed = True
            return {'installed': True, 'reused': False, 'python': config['python'], 'data_root': config['root'],
                    'previous_python': previous_python, 'initialized': True, 'sync_started': False, 'timers_enabled': False}
        finally:
            if not completed and destination.exists():
                shutil.rmtree(destination)


def uninstall():
    from strathive import schedule
    from .provider import configuration
    from .sync import writer
    schedule.disable_history()
    with access(exclusive=True):
        config = configuration()
        root = Path(config['root'])
        with writer(root) if root.exists() else nullcontext():
            removed = []
            directory = home() / 'environments'
            if directory.exists():
                for environment in directory.iterdir():
                    if managed(environment):
                        shutil.rmtree(environment)
                        removed.append(str(environment))
            downloads = home() / 'downloads'
            if downloads.exists():
                for path in downloads.iterdir():
                    if path.is_file() and not path.is_symlink() and re.fullmatch(r'[a-f0-9]{64}\.tar\.(gz|part)', path.name):
                        path.unlink()
            values = settings.settings()
            values['history'] = {**config, 'enabled': False}
            for capability in ('close', 'calendar'):
                selection = values.get('selection', {})
                if capability in selection:
                    selection[capability] = [p for p in selection[capability] if p != 'cnequity']
            settings.save(values)
            external = Path(config['python']).is_file() and not managed(Path(config['python']).parent.parent)
            return {'uninstalled': True, 'removed_environments': removed, 'data_root': str(root), 'data_retained': True,
                    'external_environment_retained': external, 'shared_duckdb_cache_retained': True}
