# Fedora package for DiscProof. Build with scripts/build-rpm.sh (from the
# current git commit) or by hand with a matching Source0 tarball.

Name:           discproof
Version:        0.1.0~dev0
Release:        1%{?dist}
Summary:        Secure, verified audio CD ripping to FLAC

# The app is GPL-3.0-or-later; the bundled Inter and JetBrains Mono fonts are
# OFL-1.1 and the bundled Lucide icons are ISC.
License:        GPL-3.0-or-later AND OFL-1.1 AND ISC
URL:            https://github.com/perfectaudiocopy/perfect-audio-copy
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

# Renamed from perfect-audio-copy: upgrade that package in place.
Obsoletes:      perfect-audio-copy < %{version}-%{release}
Provides:       perfect-audio-copy = %{version}-%{release}
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  desktop-file-utils
BuildRequires:  appstream
# for %%check
BuildRequires:  flac
BuildRequires:  python3dist(pytest)

# Not in pyproject (the wheel extra is for non-Fedora installs): use the system Qt.
Requires:       python3-pyside6
# flac/metaflac encode and add ReplayGain; gdbus sends desktop notifications.
Requires:       flac
Requires:       glib2
Recommends:     libnotify

%description
DiscProof makes bit-exact copies of audio CDs. It reads every sector
until two reads agree, re-reads damaged sectors, corrects the drive's read offset
and verifies every track against the AccurateRip database. It looks up album
details and cover art on MusicBrainz, the Cover Art Archive and Discogs, and writes
tagged FLAC files with a CUE sheet and a checksummed rip log, in Plex-friendly
folders.

%prep
%autosetup -p1 -n %{name}-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files -l discproof

desk=src/discproof/ui/desktop
install -Dpm 0644 $desk/io.github.discproof.DiscProof.desktop \
    %{buildroot}%{_datadir}/applications/io.github.discproof.DiscProof.desktop
install -Dpm 0644 $desk/discproof-rip-audio-cd.desktop \
    %{buildroot}%{_datadir}/solid/actions/discproof-rip-audio-cd.desktop
install -Dpm 0644 $desk/io.github.discproof.DiscProof.metainfo.xml \
    %{buildroot}%{_metainfodir}/io.github.discproof.DiscProof.metainfo.xml
install -Dpm 0644 src/discproof/ui/icons/io.github.discproof.DiscProof.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.discproof.DiscProof.svg

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/*.desktop
appstreamcli validate --no-net --pedantic --explain \
    %{buildroot}%{_metainfodir}/*.metainfo.xml || :
%pyproject_check_import -e 'discproof.ui*' -e discproof.app
# Core tests only: hardware tests need a drive, UI tests a display.
%pytest -m "not hardware and not ui" tests

%files -f %{pyproject_files}
%doc README.md docs/DESIGN_SYSTEM.md docs/TESTING.md
%{_bindir}/discproof
%{_bindir}/discproof-probe
%{_bindir}/discproof-drivecheck
%{_datadir}/applications/io.github.discproof.DiscProof.desktop
%{_datadir}/solid/actions/discproof-rip-audio-cd.desktop
%{_metainfodir}/io.github.discproof.DiscProof.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/io.github.discproof.DiscProof.svg

%changelog
* Sun Nov 22 2026 DiscProof authors - 0.1.0~dev0-1
- First package
