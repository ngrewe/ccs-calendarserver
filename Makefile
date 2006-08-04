##
# Makefile for CalendarServer
##
# Copyright (c) 2005-2006 Apple Computer, Inc. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# DRI: Wilfredo Sanchez, wsanchez@apple.com
##

# Project info
Project	    = CalendarServer
ProjectName = CalendarServer
UserType    = Server
ToolType    = Applications

# Include common makefile targets for B&I
include $(MAKEFILEPATH)/CoreOS/ReleaseControl/Common.make

PYTHON = /usr/bin/python
PY_INSTALL_FLAGS = --root="$(DSTROOT)" --home="$(SHAREDIR)/caldavd"

USER  = 93 # FIXME: calendar
GROUP = 93 # FIXME: calendar

#
# Build
#

.phony: $(Project) vobject Twisted setup prep

PyKerberos::      $(BuildDirectory)/PyKerberos
PyOpenDirectory:: $(BuildDirectory)/PyOpenDirectory
vobject::         $(BuildDirectory)/vobject
Twisted::         $(BuildDirectory)/Twisted
$(Project)::      $(BuildDirectory)/$(Project)

build:: PyKerberos PyOpenDirectory vobject Twisted $(Project)

setup:
	$(_v) $(Sources)/run -s

prep:: setup PyKerberos.tgz PyOpenDirectory.tgz vobject.tgz Twisted.tgz

PyKerberos PyOpenDirectory vobject $(Project)::
	@echo "Building $@..."
	$(_v) cd $(BuildDirectory)/$@ && $(Environment) $(PYTHON) setup.py build

TwistedSubEnvironment = $(Environment) PYTHONPATH="$(DSTROOT)$(SHAREDIR)/caldavd/lib/python"

Twisted::
	@echo "Building Twisted..."
	$(_v) cd $(BuildDirectory)/Twisted && $(Environment) $(PYTHON) twisted/topfiles/setup.py install $(PY_INSTALL_FLAGS)
	$(_v) cd $(BuildDirectory)/Twisted && $(TwistedSubEnvironment) $(PYTHON) twisted/web/topfiles/setup.py build
	$(_v) cd $(BuildDirectory)/Twisted && $(TwistedSubEnvironment) $(PYTHON) twisted/web2/topfiles/setup.py build

install:: build
	$(_v) cd $(BuildDirectory)/$(Project) && $(Environment) $(PYTHON) setup.py install \
	          $(PY_INSTALL_FLAGS)                                                      \
	          --install-scripts="$(USRSBINDIR)"                                        \
	          --install-data="$(ETCDIR)"
	$(_v) cd $(BuildDirectory)/PyKerberos      && $(Environment) $(PYTHON) setup.py install $(PY_INSTALL_FLAGS)
	$(_v) cd $(BuildDirectory)/PyOpenDirectory && $(Environment) $(PYTHON) setup.py install $(PY_INSTALL_FLAGS)
	$(_v) cd $(BuildDirectory)/vobject         && $(Environment) $(PYTHON) setup.py install $(PY_INSTALL_FLAGS)
	$(_v) cd $(BuildDirectory)/Twisted && $(TwistedSubEnvironment) $(PYTHON) twisted/web/topfiles/setup.py  install $(PY_INSTALL_FLAGS)
	$(_v) cd $(BuildDirectory)/Twisted && $(TwistedSubEnvironment) $(PYTHON) twisted/web2/topfiles/setup.py install $(PY_INSTALL_FLAGS)
	$(_v) for so in $$(find "$(DSTROOT)$(SHAREDIR)/caldavd/lib" -type f -name '*.so'); do $(STRIP) -Sx "$${so}"; done
	$(_v) for f in $$(find "$(DSTROOT)$(ETCDIR)" -type f ! -name '*.default'); do cp "$${f}" "$${f}.default"; done

install::
	$(_v) $(INSTALL_DIRECTORY) $(DSTROOT)$(MANDIR)/man8
	$(_v) $(INSTALL_FILE) $(Sources)/doc/caldavd.8 $(DSTROOT)$(MANDIR)/man8
	$(_v) gzip -9 -f $(DSTROOT)$(MANDIR)/man8/*.8
	$(_v) $(INSTALL_DIRECTORY) $(DSTROOT)$(NSLIBRARYDIR)/$(Project)
	$(_v) $(INSTALL_DIRECTORY) -o $(USER) -g $(GROUP) $(DSTROOT)$(NSLOCALDIR)/$(NSLIBRARYSUBDIR)/$(Project)/Documents
	$(_v) $(INSTALL_DIRECTORY) -o $(USER) -g $(GROUP) $(DSTROOT)$(VARDIR)/log/caldavd
	$(_v) $(INSTALL_DIRECTORY) $(DSTROOT)$(NSLIBRARYDIR)/LaunchDaemons
	$(_v) $(INSTALL_FILE) $(Sources)/conf/launchd.plist $(DSTROOT)$(NSLIBRARYDIR)/LaunchDaemons/org.darwin.calendarserver.plist

#
# Automatic Extract
#

$(BuildDirectory)/$(Project):
	@echo "Copying source for $(Project)..."
	$(_v) $(MKDIR) -p "$@"
	$(_v) pax -rw bin conf Makefile patches setup.py twistedcaldav "$@/"

$(BuildDirectory)/%: %.tgz
	@echo "Extracting source for $(notdir $<)..."
	$(_v) $(MKDIR) -p "$(BuildDirectory)"
	$(_v) $(RMDIR) "$@"
	$(_v) $(TAR) -C "$(BuildDirectory)" -xzf $<

%.tgz: ../%
	@echo "Archiving sources for $(notdir $<)..."
	$(_v) $(TAR) -C "$(dir $<)"        \
	          --exclude=.svn           \
	          --exclude=build          \
	          --exclude=_trial_temp    \
	          --exclude=dropin.cache   \
	          -czf $@ "$(notdir $<)"

#
# Open Source Hooey
#

OSV = /usr/local/OpenSourceVersions
OSL = /usr/local/OpenSourceLicenses

#install:: install-ossfiles

install-ossfiles::
	$(_v) $(INSTALL_DIRECTORY) $(DSTROOT)/$(OSV)
	$(_v) $(INSTALL_FILE) $(Sources)/$(ProjectName).plist $(DSTROOT)/$(OSV)/$(ProjectName).plist
	$(_v) $(INSTALL_DIRECTORY) $(DSTROOT)/$(OSL)
	$(_v) $(INSTALL_FILE) $(BuildDirectory)/$(Project)/LICENSE $(DSTROOT)/$(OSL)/$(ProjectName).txt

#
# B&I Hooey
#

buildit: prep
	@echo "Running buildit..."
	$(_v) sudo ~rc/bin/buildit $(CC_Archs) $(Sources)
