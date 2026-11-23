#!/usr/bin/env bash
# Build source and binary RPMs from the current git commit.
#   sudo dnf install rpm-build pyproject-rpm-macros python3-devel
#   scripts/build-rpm.sh            -> RPMs under ./dist/rpm/
# The Python build dependencies (hatchling, pip, ...) are generated from
# pyproject.toml during the build; if they are missing the script says which
# `dnf builddep` command installs them, then run it again.
# (Or build in a clean chroot: mock -r fedora-44-x86_64 dist/rpm/SRPMS/*.src.rpm)
set -euo pipefail
cd "$(dirname "$0")/.."

version=$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
rpmver=${version/.dev/\~dev}      # 0.1.0.dev0 -> 0.1.0~dev0 (sorts before 0.1.0)
top="$PWD/dist/rpm"
mkdir -p "$top"/{SOURCES,SPECS}

if ! git diff --quiet HEAD; then
    echo "warning: uncommitted changes are NOT included (the RPM is built from HEAD)" >&2
fi
git archive --format=tar.gz --prefix="perfect-audio-copy-$rpmver/" \
    -o "$top/SOURCES/perfect-audio-copy-$rpmver.tar.gz" HEAD
# Each build gets a newer Release (build time + commit), so `dnf upgrade` installs
# it over the previous build and `rpm -q` shows which commit is installed.
release="1.$(date -u +%Y%m%d.%H%M).g$(git rev-parse --short HEAD)"
sed -e "s/^Version:.*/Version:        $rpmver/" \
    -e "s/^Release:.*/Release:        $release%{?dist}/" \
    packaging/perfect-audio-copy.spec > "$top/SPECS/perfect-audio-copy.spec"

rm -f "$top"/SRPMS/*.buildreqs.nosrc.rpm "$top"/RPMS/noarch/*.rpm "$top"/SRPMS/*.src.rpm
set +e
rpmbuild --define "_topdir $top" -ba "$top/SPECS/perfect-audio-copy.spec"
status=$?
set -e
if [ "$status" -eq 11 ]; then
    # rpmbuild stopped at %generate_buildrequires: dependencies are missing.
    nosrc=$(ls "$top"/SRPMS/*.buildreqs.nosrc.rpm)
    echo >&2
    echo "Missing build dependencies. Install them, then run this script again:" >&2
    echo "    sudo dnf builddep $nosrc" >&2
    exit 11
fi
[ "$status" -eq 0 ] || exit "$status"
echo
echo "RPMs:"
ls -1 "$top"/RPMS/noarch/*.rpm "$top"/SRPMS/*.src.rpm
