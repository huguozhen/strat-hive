#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ "$(uname -s)-$(uname -m)" != Linux-x86_64 ]]; then
    echo "请在 x86-64 Linux 构建安装包。" >&2
    exit 1
fi
python_version=$(<.python-version)
uv python install --managed-python "$python_version"
python=$(uv python find --managed-python "$python_version")
version=$("$python" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
if [[ -n "${GITHUB_REF_NAME:-}" && "${GITHUB_REF_TYPE:-}" == tag && "$GITHUB_REF_NAME" != "v$version" ]]; then
    echo "标签必须与项目版本 v$version 一致。" >&2
    exit 1
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
stage="$work/package"
runtime="$stage/opt/strathive/python"
mkdir -p "$runtime" "$stage/DEBIAN" "$stage/usr/bin" "$stage/usr/share/strathive/skills"
cp -a "$("$python" -c 'import sys; print(sys.base_prefix)')/." "$runtime/"
bundled="$runtime/bin/python3.14"
target="$runtime/lib/python3.14/site-packages"
# 运行时可能预装 pip，清空站点包后只安装锁定的应用依赖。
rm -rf "$target"
mkdir -p "$target"
uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file "$work/requirements.txt" >/dev/null
uv build --wheel --out-dir "$work/wheels"
uv pip install --python "$bundled" --target "$target" --no-deps --require-hashes -r "$work/requirements.txt"
uv pip install --python "$bundled" --target "$target" --no-deps "$work/wheels/"*.whl
# 这些管理入口的 shebang 指向构建机，应用入口统一使用随包分发的解释器。
find "$runtime/bin" -maxdepth 1 -type f ! -name python3.14 -delete
find "$runtime/bin" -xtype l -delete
rm -rf "$target/bin"
cp -r skills/strathive-ledger "$stage/usr/share/strathive/skills/"
for history_file in install.py requirements.lock build-requirements.lock; do
    install -Dm644 "scripts/history/$history_file" "$stage/usr/share/strathive/history/$history_file"
done
install -Dm644 README.md "$stage/usr/share/doc/strathive/README.md"

cat > "$stage/usr/bin/strathive" <<'LAUNCHER'
#!/bin/sh
exec /opt/strathive/python/bin/python3.14 -I -m strathive "$@"
LAUNCHER
chmod 755 "$stage/usr/bin/strathive"
cat > "$stage/DEBIAN/control" <<CONTROL
Package: strathive
Version: $version
Architecture: amd64
Maintainer: StratHive maintainers
Section: utils
Priority: optional
Depends: libc6 (>= 2.39), libstdc++6, libgssapi-krb5-2, zlib1g, ca-certificates, tzdata
Description: StratHive personal strategy-run ledger
 Local ledger and security directory with a CLI and an Agent Skill.
CONTROL

# 用包内解释器验证 SQLite、CLI 和随包资源，防止构建机环境掩盖缺失文件。
(
    cd "$work"
    export XDG_DATA_HOME="$work/data" XDG_CACHE_HOME="$work/cache" XDG_CONFIG_HOME="$work/config"
    "$bundled" -I -m strathive db init
    "$bundled" -I -m strathive security lookup SH.510300
    "$bundled" -I -c 'import ensurepip; print(ensurepip.version())'
)
mkdir -p dist
artifact="strathive_${version}_amd64.deb"
chmod -R go-w "$stage"
dpkg-deb --root-owner-group --build "$stage" "dist/$artifact"
(cd dist && sha256sum "$artifact" > SHA256SUMS)
