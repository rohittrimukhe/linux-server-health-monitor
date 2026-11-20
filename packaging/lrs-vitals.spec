%global debug_package %{nil}
%global __os_install_post %{nil}
%global _python_bytecompile_errors_terminate_build 0

Name:           lrs-vitals
Version:        1.4.0
Release:        1%{?dist}
Summary:        LRS Vitals - Linux server health monitor
License:        Proprietary
URL:            https://www.lrsservices.example/
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3 >= 3.6
Requires:       openssl
Recommends:     python3-reportlab
Recommends:     python3-openpyxl
Suggests:       cockpit
Provides:       vitals = %{version}-%{release}
Provides:       systemhealth = %{version}-%{release}
Provides:       lshme = %{version}-%{release}
Obsoletes:      lshme < 1.3.0

%description
LRS Vitals: read-only health monitoring, reporting and alerting for Linux servers, from LRS Services.

The 'vitals' command opens a full-screen interface (in the style of nmtui) with live graphs of CPU,
memory, filesystems, mounts, network and processes, the reports already written, and every setting.
The same pages are available in a web browser after signing in.

On first use it scans the server and checks only what is really present (KVM, databases, backup products,
Cockpit, containers...). A rescan runs every Sunday at 02:00 to notice new software. Reports are written in
HTML, PDF, Excel, PowerPoint and JSON on a daily, weekly and monthly schedule and can be emailed.

The older command names 'systemhealth' and 'healthcheck' keep working.

%prep
%setup -q

%build
# nothing to build: pure Python (3.6 or newer), standard library only

%install
rm -rf %{buildroot}
install -d %{buildroot}/opt/lshme
cp -a lshme %{buildroot}/opt/lshme/
find %{buildroot}/opt/lshme -name '__pycache__' -prune -exec rm -rf {} +
install -m 0755 healthcheck %{buildroot}/opt/lshme/healthcheck
install -m 0644 README.md %{buildroot}/opt/lshme/README.md
cp -a docs %{buildroot}/opt/lshme/docs
install -d %{buildroot}/opt/lshme/config-templates
install -m 0644 config/*.conf %{buildroot}/opt/lshme/config-templates/
install -d -m 0700 %{buildroot}/etc/lshme
install -m 0600 config/*.conf %{buildroot}/etc/lshme/
install -d -m 0750 %{buildroot}/var/lib/lshme
install -d -m 0750 %{buildroot}/var/log/lshme
install -d %{buildroot}%{_bindir}
ln -s /opt/lshme/healthcheck %{buildroot}%{_bindir}/vitals
ln -s /opt/lshme/healthcheck %{buildroot}%{_bindir}/systemhealth
ln -s /opt/lshme/healthcheck %{buildroot}%{_bindir}/healthcheck
install -d %{buildroot}%{_mandir}/man1
install -m 0644 packaging/vitals.1 %{buildroot}%{_mandir}/man1/vitals.1
sed 's/^\.TH VITALS/.TH SYSTEMHEALTH/' packaging/vitals.1 > %{buildroot}%{_mandir}/man1/systemhealth.1
sed 's/^\.TH VITALS/.TH HEALTHCHECK/' packaging/vitals.1 > %{buildroot}%{_mandir}/man1/healthcheck.1

%files
%defattr(-,root,root,-)
%license LICENSE
%doc README.md CHANGELOG.md
/opt/lshme
%{_bindir}/vitals
%{_bindir}/systemhealth
%{_bindir}/healthcheck
%{_mandir}/man1/vitals.1*
%{_mandir}/man1/systemhealth.1*
%{_mandir}/man1/healthcheck.1*
%dir %attr(0700,root,root) /etc/lshme
%config(noreplace) %attr(0600,root,root) /etc/lshme/*.conf
%dir %attr(0750,root,root) /var/lib/lshme
%dir %attr(0750,root,root) /var/log/lshme

%post
if command -v restorecon >/dev/null 2>&1; then
    restorecon -R /opt/lshme /etc/lshme /var/lib/lshme /var/log/lshme >/dev/null 2>&1 || :
fi
if [ "$1" -eq 1 ]; then
    # first installation: find out what is on this server, and schedule the reports and the weekly rescan
    /opt/lshme/healthcheck scan --apply --quiet >/dev/null 2>&1 || :
    /opt/lshme/healthcheck schedule install >/dev/null 2>&1 || :
    if [ -d /usr/share/cockpit ]; then
        /opt/lshme/healthcheck cockpit-page install >/dev/null 2>&1 || :
    fi
    cat <<'BANNER'

  LRS Vitals is installed.

  Get started:
      sudo vitals                first-time setup (scans this server, then a few questions)

  Afterwards, the same command opens the live health screen:
      vitals                     live graphs, reports and settings
      vitals help                all commands and keys
      man vitals

  The server is scanned every Sunday at 02:00 to notice new software.

BANNER
else
    # upgrade: refresh the schedules this installation owns
    if ls /etc/systemd/system/lshme-*.timer >/dev/null 2>&1 || [ -f /etc/cron.d/lshme ]; then
        /opt/lshme/healthcheck schedule install >/dev/null 2>&1 || :
    fi
    if [ -d /usr/share/cockpit/lshme ]; then
        /opt/lshme/healthcheck cockpit-page install >/dev/null 2>&1 || :
    fi
fi

%preun
if [ "$1" -eq 0 ]; then
    # complete removal: take down what this package created (settings, history and reports are kept)
    /opt/lshme/healthcheck schedule remove >/dev/null 2>&1 || :
    /opt/lshme/healthcheck watch remove >/dev/null 2>&1 || :
    /opt/lshme/healthcheck web remove >/dev/null 2>&1 || :
    /opt/lshme/healthcheck cockpit-page remove >/dev/null 2>&1 || :
fi

%postun
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload >/dev/null 2>&1 || :
    if [ "$1" -ge 1 ]; then
        # upgrade: running services pick up the new code
        systemctl try-restart lshme-watch.service lshme-web.service >/dev/null 2>&1 || :
    fi
fi

%changelog
* Fri Nov 20 2026 LRS Services <support@lrsservices.example> - 1.4.0-1
- Daily, weekly and monthly reports, alert rules, Servers page, Linux sign-in, customer portal, tool self-check (see CHANGELOG.md)

* Fri Nov 20 2026 LRS Services <support@lrsservices.example> - 1.3.0-1
- Renamed to LRS Vitals; the command is now 'vitals' (systemhealth and healthcheck still work)
- Full-screen live dashboard, report browser, run-now, server scan and first-time setup
- Web interface with sign-in, live dashboard, reports and a settings editor
- Server scan: only modules that apply are checked and reported; weekly rescan Sunday 02:00
- Report summary, navigation and controls
