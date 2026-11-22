# Fedora package for Perfect Audio Copy. Build with scripts/build-rpm.sh (from the
# current git commit) or by hand with a matching Source0 tarball.

Name:           perfect-audio-copy
Version:        0.1.0~dev0
Release:        1%{?dist}
Summary:        Secure, verified audio CD ripping to FLAC

# The app is GPL-3.0-or-later; the bundled Inter and JetBrains Mono fonts are
# OFL-1.1 and the bundled Lucide icons are ISC.
License:        GPL-3.0-or-later AND OFL-1.1 AND ISC
# PLACEHOLDER until the project has a public home (see PROJECT_URL in the source).
URL:            https://perfect-audio-copy.invalid
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
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
Perfect Audio Copy makes bit-exact copies of audio CDs. It reads every sector
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
%pyproject_save_files -l perfect_audio_copy

desk=src/perfect_audio_copy/ui/desktop
install -Dpm 0644 $desk/io.github.perfectaudiocopy.PerfectAudioCopy.desktop \
    %{buildroot}%{_datadir}/applications/io.github.perfectaudiocopy.PerfectAudioCopy.desktop
install -Dpm 0644 $desk/perfect-audio-copy-rip-audio-cd.desktop \
    %{buildroot}%{_datadir}/solid/actions/perfect-audio-copy-rip-audio-cd.desktop
install -Dpm 0644 $desk/io.github.perfectaudiocopy.PerfectAudioCopy.metainfo.xml \
    %{buildroot}%{_metainfodir}/io.github.perfectaudiocopy.PerfectAudioCopy.metainfo.xml
install -Dpm 0644 src/perfect_audio_copy/ui/icons/io.github.perfectaudiocopy.PerfectAudioCopy.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.perfectaudiocopy.PerfectAudioCopy.svg

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/*.desktop
appstreamcli validate --no-net --pedantic --explain \
    %{buildroot}%{_metainfodir}/*.metainfo.xml || :
%pyproject_check_import -e 'perfect_audio_copy.ui*' -e perfect_audio_copy.app
# Core tests only: hardware tests need a drive, UI tests a display.
%pytest -m "not hardware and not ui" tests

%files -f %{pyproject_files}
%doc README.md docs/DESIGN_SYSTEM.md docs/TESTING.md
%{_bindir}/perfect-audio-copy
%{_bindir}/perfect-audio-copy-probe
%{_bindir}/perfect-audio-copy-drivecheck
%{_datadir}/applications/io.github.perfectaudiocopy.PerfectAudioCopy.desktop
%{_datadir}/solid/actions/perfect-audio-copy-rip-audio-cd.desktop
%{_metainfodir}/io.github.perfectaudiocopy.PerfectAudioCopy.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/io.github.perfectaudiocopy.PerfectAudioCopy.svg

%changelog
* Sun Nov 22 2026 Perfect Audio Copy authors - 0.1.0~dev0-1
- First package
