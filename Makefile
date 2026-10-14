PKG_NAME := fleet
DIST_DIR  := dist
LOCAL_COCKPIT_DIR := $(HOME)/.local/share/cockpit
DESTDIR ?=

COCKPIT_DIR := $(DESTDIR)/usr/share/cockpit/$(PKG_NAME)
LIB_DIR := $(DESTDIR)/usr/lib/cockpit-fleet
LIBEXEC_DIR := $(DESTDIR)/usr/libexec/cockpit-fleet
SHARE_DIR := $(DESTDIR)/usr/share/cockpit-fleet
COLLECTION := ansible_collections/lineadicomando/fleet

-include Makefile.local

.PHONY: all clean distclean build watch install install-files uninstall devel-install devel-uninstall check check-ts check-py deb pot po-compile help

all: build

build: ## Build bundle in dist/
	node build.js

watch: ## Watch mode (auto-rebuild)
	node build.js --watch

clean: ## Remove build artifacts
	rm -rf $(DIST_DIR)

distclean: clean ## Remove build artifacts and node_modules
	rm -rf node_modules

devel-install: build ## Symlink dist/ into local cockpit directory (frontend only)
	mkdir -p $(LOCAL_COCKPIT_DIR)
	ln -sfn $(CURDIR)/$(DIST_DIR) $(LOCAL_COCKPIT_DIR)/$(PKG_NAME)

devel-uninstall: ## Remove local cockpit symlink
	rm -f $(LOCAL_COCKPIT_DIR)/$(PKG_NAME)

install: build install-files ## Build and install everything under DESTDIR (needs root)

install-files: ## Install the built bundle, backend and system files under DESTDIR
	install -d $(COCKPIT_DIR)
	cp -a $(DIST_DIR)/. $(COCKPIT_DIR)/
	install -d $(LIB_DIR)/cockpit_fleet
	install -m 0644 backend/cockpit_fleet/*.py $(LIB_DIR)/cockpit_fleet/
	install -d $(LIBEXEC_DIR)
	install -m 0755 backend/libexec/* $(LIBEXEC_DIR)/
	install -D -m 0755 backend/bin/fleet $(DESTDIR)/usr/bin/fleet
	install -D -m 0644 system/fleet.bash-completion $(DESTDIR)/usr/share/bash-completion/completions/fleet
	install -D -m 0644 system/fleet.1 $(DESTDIR)/usr/share/man/man1/fleet.1
	install -d $(SHARE_DIR)/collections/$(COLLECTION)/plugins/callback
	install -m 0644 collection/$(COLLECTION)/galaxy.yml collection/$(COLLECTION)/README.md \
		$(SHARE_DIR)/collections/$(COLLECTION)/
	install -m 0644 collection/$(COLLECTION)/plugins/callback/*.py \
		$(SHARE_DIR)/collections/$(COLLECTION)/plugins/callback/
	cp -r --no-preserve=mode,ownership collection/$(COLLECTION)/roles collection/$(COLLECTION)/playbooks \
		$(SHARE_DIR)/collections/$(COLLECTION)/
	install -D -m 0644 system/cockpit-fleet-run@.service \
		$(DESTDIR)/usr/lib/systemd/system/cockpit-fleet-run@.service
	install -D -m 0644 system/cockpit-fleet-finish@.service \
		$(DESTDIR)/usr/lib/systemd/system/cockpit-fleet-finish@.service
	install -m 0644 system/cockpit-fleet-scheduler.service system/cockpit-fleet-scheduler.timer \
		system/cockpit-fleet-notify.service system/cockpit-fleet-notify.path \
		$(DESTDIR)/usr/lib/systemd/system/
	install -D -m 0644 system/cockpit-fleet.sysusers $(DESTDIR)/usr/lib/sysusers.d/cockpit-fleet.conf
	install -D -m 0644 system/cockpit-fleet.tmpfiles $(DESTDIR)/usr/lib/tmpfiles.d/cockpit-fleet.conf
	install -D -m 0644 system/config.toml $(DESTDIR)/etc/cockpit-fleet/config.toml
	install -D -m 0440 system/cockpit-fleet.sudoers $(DESTDIR)/etc/sudoers.d/cockpit-fleet

uninstall: ## Remove the files installed by install-files (keeps data and secrets)
	rm -rf $(COCKPIT_DIR) $(LIB_DIR) $(LIBEXEC_DIR) $(SHARE_DIR)
	rm -f $(DESTDIR)/usr/lib/systemd/system/cockpit-fleet-run@.service \
		$(DESTDIR)/usr/lib/systemd/system/cockpit-fleet-finish@.service \
		$(DESTDIR)/usr/lib/systemd/system/cockpit-fleet-scheduler.service \
		$(DESTDIR)/usr/lib/systemd/system/cockpit-fleet-scheduler.timer \
		$(DESTDIR)/usr/lib/systemd/system/cockpit-fleet-notify.service \
		$(DESTDIR)/usr/lib/systemd/system/cockpit-fleet-notify.path \
		$(DESTDIR)/usr/lib/sysusers.d/cockpit-fleet.conf \
		$(DESTDIR)/usr/lib/tmpfiles.d/cockpit-fleet.conf \
		$(DESTDIR)/etc/sudoers.d/cockpit-fleet \
		$(DESTDIR)/usr/bin/fleet $(DESTDIR)/usr/share/bash-completion/completions/fleet \
		$(DESTDIR)/usr/share/man/man1/fleet.1

check: check-ts check-py ## Run all unit tests

check-ts: ## Run frontend unit tests
	npm test

check-py: ## Run backend unit tests
	cd backend && python3 -m unittest discover -s tests

deb: build ## Build the .deb in a Debian 13 container, into deb/
	scripts/build-deb.sh

pot: ## Extract translatable strings from src/ into src/locales/*/translation.json
	npm run pot

po-compile: ## Compile po/it.po into src/locales/it/translation.json
	npm run po-compile

help:
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk -F ':.*?## ' '{printf "  %-18s %s\n", $$1, $$2}'
