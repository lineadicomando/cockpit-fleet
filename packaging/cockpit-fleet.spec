Name:           cockpit-fleet
Version:        0.0.1
Release:        1%{?dist}
Summary:        Cockpit plugin to run Ansible and watch a fleet of nodes
License:        LGPL-2.1-or-later
URL:            https://github.com/lineadicomando/cockpit-fleet
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  make
BuildRequires:  systemd-rpm-macros

Requires:       cockpit-bridge >= 337
# AlmaLinux 10 has ansible-core 2.16 and Python 3.12.
Requires:       ansible-core >= 2.16
Requires:       python3 >= 3.12
Requires:       systemd >= 257
Requires:       sudo
Requires:       git-core
Requires:       openssh-clients
Recommends:     ansible
Recommends:     sshpass
Suggests:       ansible-lint

%description
Runs Ansible playbooks from the Cockpit web console, with projects,
inventories, credentials, schedules and notifications, and uses Ansible
to collect health, inventory and update information from the managed
nodes without installing an agent on them.

Runs execute in sandboxed systemd units under a dedicated unprivileged
account; no daemon is left running and no network port is opened.

%prep
%autosetup

# The bundle is built beforehand with npm (make build): the package build has
# no network access and only packs the result.
%build
test -f dist/index.js || { echo "dist/ is missing: run 'make build' first"; exit 1; }

%install
%{__make} install-files DESTDIR=%{buildroot}
install -d %{buildroot}%{_sysconfdir}/cockpit-fleet/secrets/notify \
    %{buildroot}%{_sharedstatedir}/cockpit-fleet/runs \
    %{buildroot}%{_sharedstatedir}/cockpit-fleet/projects \
    %{buildroot}/run/cockpit-fleet/answers
install -D -m 0644 system/cockpit-fleet.preset %{buildroot}%{_presetdir}/80-cockpit-fleet.preset

# rpm creates the account and groups from the sysusers.d file before the files
# that belong to them are installed.
%post
%tmpfiles_create %{name}.conf
%systemd_post cockpit-fleet-scheduler.timer cockpit-fleet-notify.path
if [ $1 -eq 1 ]; then
    systemctl start cockpit-fleet-scheduler.timer cockpit-fleet-notify.path >/dev/null 2>&1 || :
fi

%preun
%systemd_preun cockpit-fleet-scheduler.timer cockpit-fleet-notify.path

%postun
%systemd_postun_with_restart cockpit-fleet-scheduler.timer cockpit-fleet-notify.path

%files
%license LICENSE
%doc README.md SECURITY.md
%{_datadir}/cockpit/fleet
%{_prefix}/lib/cockpit-fleet
%{_libexecdir}/cockpit-fleet
%{_datadir}/cockpit-fleet
%{_bindir}/fleet
%{_datadir}/bash-completion/completions/fleet
%{_mandir}/man1/fleet.1*
%{_unitdir}/cockpit-fleet-run@.service
%{_unitdir}/cockpit-fleet-finish@.service
%{_unitdir}/cockpit-fleet-scheduler.service
%{_unitdir}/cockpit-fleet-scheduler.timer
%{_unitdir}/cockpit-fleet-notify.service
%{_unitdir}/cockpit-fleet-notify.path
%{_presetdir}/80-cockpit-fleet.preset
%{_sysusersdir}/cockpit-fleet.conf
%{_tmpfilesdir}/cockpit-fleet.conf
%dir %{_sysconfdir}/cockpit-fleet
# Also created by the tmpfiles.d file, with the same modes.
%dir %attr(0750,root,cockpit-fleet) %{_sysconfdir}/cockpit-fleet/secrets
%dir %attr(0750,root,cockpit-fleet) %{_sysconfdir}/cockpit-fleet/secrets/notify
%dir %attr(0750,cockpit-fleet,cockpit-fleet) %{_sharedstatedir}/cockpit-fleet
%dir %attr(0750,cockpit-fleet,cockpit-fleet) %{_sharedstatedir}/cockpit-fleet/runs
%dir %attr(0750,cockpit-fleet,cockpit-fleet) %{_sharedstatedir}/cockpit-fleet/projects
%ghost %dir %attr(0755,root,root) /run/cockpit-fleet
%ghost %dir %attr(0770,root,cockpit-fleet) /run/cockpit-fleet/answers
%config(noreplace) %{_sysconfdir}/cockpit-fleet/config.toml
%attr(0440,root,root) %config(noreplace) %{_sysconfdir}/sudoers.d/cockpit-fleet

%changelog
* Thu Oct 08 2026 Alessandro Gagliano <alessandro.gagliano@gmail.com> - 0.0.1-1
- Packaging skeleton: empty Cockpit page, service account, role groups,
  run unit and the helper that starts it.
