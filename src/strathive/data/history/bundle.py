"""The optional component's versioned, hash-checked wheel bundle contract."""
import hashlib
import json
from pathlib import Path
import platform
import re
import sys
import tarfile

from strathive.core.rules import require
from . import COMMIT

RECEIPT = 'strathive-history-install.json'
MANIFEST = Path(__file__).resolve().parents[2] / 'resources/history/component.json'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def manifest(path=None):
    try:
        value = json.loads(Path(path or MANIFEST).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        from strathive.core.rules import LedgerError
        raise LedgerError('history_manifest_unavailable', '无法读取历史组件的发布清单。') from exc
    require(isinstance(value, dict), '历史组件清单格式无效。', 'history_bundle_invalid')
    require(value.get('format') == 1 and value.get('component') == 'history'
            and value.get('commit') == COMMIT and value.get('cnequity_version') == '0.25.0',
            '历史组件清单与本版 StratHive 不匹配。', 'history_version_mismatch')
    require(value.get('python') == f'{sys.version_info.major}.{sys.version_info.minor}'
            and value.get('platform') == f'{sys.platform}_{platform.machine()}',
            '历史组件与当前 Python 或平台不匹配。', 'history_platform_mismatch')
    artifact = value.get('artifact')
    require(artifact is None or (isinstance(artifact, dict) and isinstance(artifact.get('sha256'), str)
            and re.fullmatch(r'[a-f0-9]{64}', artifact['sha256'])),
            '历史组件制品校验清单无效。', 'history_bundle_invalid')
    return value


def _unpack(archive, destination, expected):
    """Accept only wheels and the hashed requirements, with no links or traversal."""
    require(digest(archive) == expected['artifact']['sha256'], '历史组件 SHA-256 校验失败。', 'history_checksum_failed')
    home = Path(destination)
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        names = [m.name for m in members]
        require(len(names) == len(set(names)) and len(names) <= 200,
                '历史组件文件清单无效。', 'history_bundle_invalid')
        for member in members:
            path = Path(member.name)
            valid = member.name == 'requirements.lock' or (
                len(path.parts) == 2 and path.parts[0] == 'wheels' and path.suffix == '.whl')
            require(member.isfile() and valid and not path.is_absolute() and '..' not in path.parts
                    and member.size <= 300 * 1024**2, '历史组件包含非预期文件。', 'history_bundle_invalid')
        require(sum(m.size for m in members) <= 2 * 1024**3 and 'requirements.lock' in names,
                '历史组件不完整或超过大小限制。', 'history_bundle_invalid')
        bundle.extractall(home, members=members, filter='data')
    requirements = (home / 'requirements.lock').read_text()
    require(bool(requirements.strip()), '历史组件依赖清单为空。', 'history_bundle_invalid')
    # All install targets must be exact named wheels. pip separately checks every hash.
    for line in requirements.splitlines():
        require(bool(re.fullmatch(r'[A-Za-z0-9_.-]+==[^\s;]+ --hash=sha256:[a-f0-9]{64}', line)),
                '历史组件依赖清单格式无效。', 'history_bundle_invalid')
    return home


def unpack(archive, destination, expected):
    from strathive.core.rules import LedgerError
    try:
        return _unpack(archive, destination, expected)
    except (OSError, tarfile.TarError) as exc:
        raise LedgerError('history_bundle_invalid', '无法读取历史组件制品；请重新下载或检查离线文件。') from exc
