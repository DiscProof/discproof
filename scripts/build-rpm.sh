#!/usr/bin/env bash
# Build source and binary RPMs from the current git commit.
#   sudo dnf install rpm-build pyproject-rpm-macros python3-devel
#   sudo dnf builddep packaging/perfect-audio-copy.spec
#   scripts/build-rpm.sh            -> RPMs under ./dist/rpm/
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
sed "s/^Version:.*/Version:        $rpmver/" packaging/perfect-audio-copy.spec \
    > "$top/SPECS/perfect-audio-copy.spec"

rpmbuild --define "_topdir $top" -ba "$top/SPECS/perfect-audio-copy.spec"
echo "RPMs: $top/RPMS/noarch/ and $top/SRPMS/"
